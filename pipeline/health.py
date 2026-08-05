"""Publish gates, the health verdict, and what alerting reads.

**Health gates run before anything is published.** The failure that bites is not
a crash — it is a source quietly going wrong while the run succeeds — so this
module can block publication and alert instead. It is the last thing between the
pipeline and a subscriber, and the one place in the project allowed to say "no".

Baseline gates
--------------------------------------------------------------------------

- **Per source: zero after non-zero.** A source that produced events last night
  and none tonight blocks publication, carries forward, and alerts.
- **Global: more than 40% night over night.** The whole calendar shrinking by
  that much is never a normal Tuesday.
- **Sanity window.** ``start_utc`` in the past or more than two years out is
  dropped and logged; an empty title, or one over 200 characters, is
  quarantined. See *Who owns the event-level rules* below.

Counting events is necessary but not sufficient. The 2026-08-05 source survey
found four cases a count-based gate gets wrong, each with a per-source override
in ``sources.yaml``:

- ``max_stale_days`` — an abandoned feed that keeps generating. Noisebridge's old
  gCal has 28 VEVENTs, dead since Jan 2024, five RRULEs with **no ``UNTIL``**: it
  invents ~5 events a week forever at a constant rate and passes every
  count-based gate. Checked against ``LAST-MODIFIED``. This is the important one
  and the least obvious: without it the project publishes fiction indefinitely
  while reporting perfect health.
- ``allow_zero`` — legitimately empty. The Box Shop runs ~8-12 events a year and
  Lower 48 has no machine-readable source at all. Without this they alert monthly
  and everyone learns to ignore alerts.
- ``ignore_count_drop`` — capped or truncated feed. Hacker Dojo's Meetup iCal has
  a hard 10-event cap and no pagination, so nightly deltas are noise. It does
  **not** exempt the source from the zero gate: the registry note is explicit,
  "only alert on 0 after a run with >0".
- ``require_nonzero_once`` — never yet non-zero. Humanmade's Eventbrite organizer
  and Hacker Dojo's Luma page sit at 0, so "went to zero" can never fire while a
  naive alert fires nightly. Answered by
  :meth:`~pipeline.store.Store.has_ever_returned_events`, and it stops exempting
  the source the moment that becomes true.

Two further rules from the same survey
--------------------------------------------------------------------------

**Count post-expansion events inside the horizon, not VEVENTs.** Sequoia Fabrica
has 89 VEVENTs and ~7 live ones; Maker Nexus has 3645 and 171; one feed is 5057
and 73. Raw counts say almost nothing about whether a feed is healthy, so every
gate here reads :attr:`~pipeline.cli.SourceRecord.horizon_count` and nothing in
this module ever reads ``raw_count``.

**A short publishing horizon is not a decline.** Maker Nexus posts 4-8 weeks
ahead (Aug 112, Sep 43, Oct 16), so the far end of a 120-day window is
legitimately empty every night. That is why a *per-source* count drop is
alert-only and never blocking: the only per-source condition that stops the
presses is a feed that went to zero, which is unambiguous. Blocking a whole
night's publication because one space's publishing horizon breathes would train
everyone to ignore the gate, which is the same failure as alerting monthly on
The Box Shop.

Who owns the event-level rules
--------------------------------------------------------------------------

The sanity window and the title rules already exist in
:mod:`pipeline.normalize` (issue 0009), and this module deliberately does not
re-drop what normalize dropped. The split:

- **normalize owns them at source scope.** It sees the raw event, it drops or
  quarantines, and its counts land in that source's record. Those numbers are
  the diagnosis of one feed.
- **health owns them at publish scope.** :func:`audit_events` re-applies the
  same two rules to the set that is actually about to be published — after
  carry-forward replay, after dedupe — and reports what it removed in the
  verdict's own counters. In a healthy run it removes nothing, because normalize
  already did.

So the counts never double: ``record.dropped_count`` is normalize's,
``verdict.dropped_count`` is the gate's, and a non-zero gate count means
something reached publication without passing through normalize — a carry-forward
replay of a row written by an older build, or a future adapter that forgot. It
is logged loudly and the events are withheld, which is exactly "drop and log"
and "quarantine". Neither one blocks the run: the stated remedy is to remove the
event, and removing it is what happens.

Three interactions that are easy to get backwards
--------------------------------------------------------------------------

**A 304 is not a zero and is not stale.** Issue 0014 made ``reused_unchanged``
(HTTP 304, the stored set replayed because the server says it is current) a
different thing from ``carried_forward`` (the source failed). A 304 is the
healthiest outcome there is, and it is *defined* by nothing having changed, so it
is exempt from both the zero gate and ``max_stale_days``. Penalizing a source for
staleness on the one response that means "nothing changed" would gate against the
optimization working.

**A carried-forward source does not also trip the gates.** It is already
degraded, already visible in ``health.json``, and already on the three-night
escalation clock in :mod:`pipeline.carry_forward`. Blocking publication for it
would take the other ten spaces off the calendar to punish one transient 503 —
the precise inversion of the invariant that a failed source must never silently
delete a space. It is recorded as :attr:`GateOutcome.DEGRADED`, it does not
block, and it is not double-alerted here because carry-forward already alerted.
When escalation stops the replay on night three the events genuinely disappear,
and at that point the **global** gate is free to notice, which is the right level
for "we lost a material fraction of the calendar".

**A ``blocked`` source is not a failure of ours.** ``robots.txt`` disallowed the
fetch; we honored it. CLAUDE.md: a disallow means we do not fetch it, not that we
find an equivalent route. It contributes zero events forever by design, so it is
:attr:`GateOutcome.EXEMPT` — never blocking, and never alerting either, because
an alert that fires every night for a state nobody intends to change is noise
that buries the alerts that matter. It stays visible as a source-level verdict
for issue 0017 to render.

The verdict
--------------------------------------------------------------------------

:func:`evaluate_health` returns a :class:`HealthVerdict`: whether publication
proceeds, the global reasons, one :class:`SourceVerdict` per source, and the
audited event set. Issue 0017 writes it into ``health.json``; issue 0032 alerts
on :attr:`HealthVerdict.alerts`. The CLI exits :data:`~pipeline.cli.EXIT_HEALTH_BLOCKED`
(1) when :attr:`HealthVerdict.blocks_publication` is true, and writes nothing.

Implemented by issue 0016. Consumed by issues 0017 and 0032.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

from pipeline.config import Health, Registry, Space
from pipeline.normalize import (
    MAX_FUTURE_DAYS,
    MAX_TITLE_CHARS,
    DropReason,
    Event,
    QuarantineReason,
    day_start_utc,
)

LOG = logging.getLogger("pipeline.health")

# --------------------------------------------------------------------------- knobs

#: The issue that owns these gates. Carried on the verdict so ``health.json``
#: can say where the decision came from.
GATE_ISSUE = "0016"

#: Global night-over-night drop that blocks publication. CLAUDE.md's invariant
#: names 40%, and "more than" is strict: exactly 40% publishes.
GLOBAL_DROP_THRESHOLD = 0.40

#: Per-source night-over-night drop that is worth **saying** but never worth
#: blocking for. Higher than the global threshold on purpose: one feed is far
#: noisier than the sum of eleven, and Maker Nexus's 4-8 week publishing horizon
#: is a legitimate source of movement at the far end of a 120-day window.
SOURCE_DROP_THRESHOLD = 0.50

#: How far back :func:`previous_source_count` scans for the last night this
#: source actually answered. Two weeks: long enough to see through a failure
#: streak, short enough that a source revived after a month is treated as new.
SOURCE_HISTORY_LIMIT = 14

#: How far back :func:`previous_total` scans for a comparable run.
RUN_HISTORY_LIMIT = 14

#: Record statuses whose counts are meaningful tonight.
RAN_STATUSES = frozenset({"ok", "not_modified"})

#: Record statuses that mean the source never got as far as producing a count.
FAILED_STATUSES = frozenset({"failed", "error"})


# --------------------------------------------------------------------------- reasons


class GateOutcome(str, Enum):
    """What the gates concluded about one source."""

    #: Evaluated, and fine.
    OK = "ok"
    #: An override or a documented policy took it out of scope tonight.
    EXEMPT = "exempt"
    #: Something is wrong and it is already being reported elsewhere. Does not
    #: block: carry-forward and escalation own this path.
    DEGRADED = "degraded"
    #: This source is why nothing is being published.
    BLOCKED = "blocked"
    #: Not in the run set at all (disabled, TODO, adapter not implemented).
    NOT_EVALUATED = "not_evaluated"


@dataclass(frozen=True)
class GateReason:
    """One thing a gate found, as data rather than as a sentence to re-parse.

    ``code`` is the stable handle issue 0032 keys its alert policy on; ``message``
    is what a human reads at 03:15. The two flags are separate because they are
    genuinely different questions: an escalated source alerts without blocking,
    and a never-yet-non-zero source does neither.
    """

    code: str
    message: str
    blocking: bool = False
    alert: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "blocking": self.blocking,
            "alert": self.alert,
        }

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        return self.message


def _messages(reasons: Iterable[GateReason], *, blocking: bool | None = None) -> tuple[str, ...]:
    return tuple(
        reason.message
        for reason in reasons
        if blocking is None or reason.blocking is blocking
    )


# --------------------------------------------------------------------------- config


def merge_health(space: Space | None, source: Any | None) -> Health:
    """Combine a space's ``health`` block with one source's.

    Booleans are OR-ed: a space-level ``allow_zero`` (The Box Shop, Lower 48)
    covers every feed it owns, and a source may opt in on its own without the
    space saying anything. ``max_stale_days`` takes the source's value when it
    sets one and the space's otherwise — the tighter, more specific statement
    wins, and nothing silently loosens a gate somebody wrote deliberately.
    """
    space_health = getattr(space, "health", None) or Health()
    source_health = getattr(source, "health", None) or Health()
    stale = source_health.max_stale_days
    if stale is None:
        stale = space_health.max_stale_days
    return Health(
        allow_zero=space_health.allow_zero or source_health.allow_zero,
        ignore_count_drop=space_health.ignore_count_drop or source_health.ignore_count_drop,
        require_nonzero_once=(
            space_health.require_nonzero_once or source_health.require_nonzero_once
        ),
        max_stale_days=stale,
    )


def health_index(registry: Registry | None) -> dict[str, Health]:
    """``{space_id:label: Health}`` for the whole registry, already merged."""
    if registry is None:
        return {}
    index: dict[str, Health] = {}
    for space in registry.spaces:
        for source in space.sources:
            label = source.label or source.adapter
            index[f"{space.id}:{label}"] = merge_health(space, source)
    return index


# --------------------------------------------------------------------------- history


def previous_source_count(store: Any, space_id: str, label: str, *, run_id: int | None) -> int | None:
    """This source's ``horizon_count`` on the last night it actually answered.

    ``None`` when there is no prior run to compare against, which is a *first
    night*, not a decline — a brand new source must not block the calendar.

    "The last night it answered" rather than literally the previous run: a
    source that failed last night recorded a zero it never produced, and letting
    that become the baseline would mean a feed which fails once and then starts
    returning an empty document forever never trips the gate again. The failure
    itself is not lost — carry-forward reports it and escalates it.

    Tonight's own row is excluded by ``run_id``. The run loop records source runs
    before the gates run, so it is already in the table.
    """
    history = getattr(store, "source_history", None)
    if not callable(history):
        return None
    rows = history(space_id, label, limit=SOURCE_HISTORY_LIMIT)
    prior = [row for row in rows if run_id is None or row.run_id != run_id]
    if not prior:
        return None
    for row in prior:
        if row.status in RAN_STATUSES:
            return int(row.horizon_count)
    return int(prior[0].horizon_count)


def previous_total(store: Any, *, run_id: int | None) -> tuple[int | None, int | None]:
    """``(event_count, run_id)`` of the last comparable run, or ``(None, None)``.

    Comparable means: not tonight, not a dry run, not a ``--space`` run, and it
    produced events. A single-space run holds one space's worth of events by
    construction, so comparing a full run against one would report a 90% drop
    every time and a ``--space`` run would poison tomorrow's baseline.
    """
    runs = getattr(store, "runs", None)
    if not callable(runs):
        return None, None
    for row in runs(limit=RUN_HISTORY_LIMIT):
        if run_id is not None and row.run_id == run_id:
            continue
        if row.dry_run or row.space_filter:
            continue
        if row.event_count <= 0:
            continue
        return int(row.event_count), int(row.run_id)
    return None, None


def _ever_nonzero(store: Any, space_id: str, label: str) -> bool:
    checker = getattr(store, "has_ever_returned_events", None)
    if not callable(checker):
        return False
    return bool(checker(space_id, label))


def drop_fraction(previous: int | None, current: int) -> float | None:
    """How much of ``previous`` is gone. ``None`` when there is no baseline."""
    if previous is None or previous <= 0:
        return None
    if current >= previous:
        return 0.0
    return (previous - current) / previous


# --------------------------------------------------------------------------- event audit


@dataclass(frozen=True)
class EventAudit:
    """The last-line re-check of the two event-level rules, at publish scope.

    See *Who owns the event-level rules* in the module docstring: normalize
    already applied both, so a healthy run finds nothing here and the counts do
    not double.
    """

    events: tuple[Event, ...] = ()
    dropped: tuple[tuple[str, DropReason, str], ...] = ()
    quarantined: tuple[tuple[str, QuarantineReason], ...] = ()

    @property
    def dropped_count(self) -> int:
        return len(self.dropped)

    @property
    def quarantined_count(self) -> int:
        return len(self.quarantined)

    @property
    def clean(self) -> bool:
        return not self.dropped and not self.quarantined

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_count": len(self.events),
            "dropped_count": self.dropped_count,
            "quarantined_count": self.quarantined_count,
            "dropped": [
                {"uid": uid, "reason": reason.value, "detail": detail}
                for uid, reason, detail in self.dropped
            ],
            "quarantined": [
                {"uid": uid, "reason": reason.value} for uid, reason in self.quarantined
            ],
        }


def audit_events(
    events: Sequence[Event],
    *,
    now: dt.datetime,
    timezone: str = "America/Los_Angeles",
) -> EventAudit:
    """Re-apply the sanity window and the title rules to the publish set.

    Same thresholds as :mod:`pipeline.normalize` — :data:`MAX_FUTURE_DAYS` and
    :data:`MAX_TITLE_CHARS` are imported rather than restated, so the two layers
    cannot drift into disagreeing about what "two years out" means.

    "In the past" is measured against the start of today in the event's own
    zone, not against ``now``. The run starts at 03:15 and an event at 10:00 this
    morning is still an event.
    """
    kept: list[Event] = []
    dropped: list[tuple[str, DropReason, str]] = []
    quarantined: list[tuple[str, QuarantineReason]] = []
    ceiling = now + dt.timedelta(days=MAX_FUTURE_DAYS)

    for event in events:
        if not event.title:
            quarantined.append((event.uid, QuarantineReason.EMPTY_TITLE))
            continue
        if len(event.title) > MAX_TITLE_CHARS:
            quarantined.append((event.uid, QuarantineReason.TITLE_TOO_LONG))
            continue

        zone = event.tz or timezone
        floor = day_start_utc(now.astimezone(_zoneinfo(zone)).date(), zone)
        start = event.start_utc
        if start is None:
            dropped.append((event.uid, DropReason.NO_START, "no start_utc"))
            continue
        if event.all_day and event.end_date is not None:
            last = day_start_utc(event.end_date + dt.timedelta(days=1), zone)
            if last <= floor:
                dropped.append(
                    (event.uid, DropReason.PAST, f"ended {event.end_date.isoformat()}")
                )
                continue
        elif start < floor:
            dropped.append((event.uid, DropReason.PAST, f"started {start.isoformat()}"))
            continue
        if start > ceiling:
            dropped.append(
                (
                    event.uid,
                    DropReason.TOO_FAR,
                    f"starts {start.isoformat()}, beyond {MAX_FUTURE_DAYS} days",
                )
            )
            continue
        kept.append(event)

    return EventAudit(
        events=tuple(kept), dropped=tuple(dropped), quarantined=tuple(quarantined)
    )


def _zoneinfo(name: str) -> dt.tzinfo:
    try:
        return ZoneInfo(name)
    except Exception:  # pragma: no cover - a stored row with a zone we lack
        return dt.timezone.utc


# --------------------------------------------------------------------------- verdicts


@dataclass(frozen=True)
class SourceVerdict:
    """What the gates concluded about one source, as data.

    Issue 0017 renders this; issue 0032 alerts on :attr:`alerts`. Every number a
    reader would want in order to disagree with the verdict is on it, which is
    the point: a gate that says "blocked" without showing 12 -> 0 is a gate
    nobody trusts twice.
    """

    space_id: str
    label: str
    status: str = ""
    outcome: GateOutcome = GateOutcome.OK
    reasons: tuple[GateReason, ...] = ()

    #: The gate metric. Post-expansion events inside the horizon, never raw.
    horizon_count: int = 0
    #: Survivors of the registry's filters. Reported, never gated on.
    event_count: int = 0
    previous_count: int | None = None

    stale_days: float | None = None
    max_stale_days: int | None = None
    ever_nonzero: bool = False
    carried_forward: bool = False
    reused_unchanged: bool = False
    escalated: bool = False
    #: The override names that actually applied tonight.
    overrides: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return f"{self.space_id}:{self.label}"

    @property
    def blocked(self) -> bool:
        return any(reason.blocking for reason in self.reasons)

    @property
    def messages(self) -> tuple[str, ...]:
        return _messages(self.reasons)

    @property
    def blocking_reasons(self) -> tuple[str, ...]:
        return _messages(self.reasons, blocking=True)

    @property
    def alerts(self) -> tuple[str, ...]:
        return tuple(reason.message for reason in self.reasons if reason.alert)

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(reason.code for reason in self.reasons)

    @property
    def delta(self) -> int | None:
        if self.previous_count is None:
            return None
        return self.horizon_count - self.previous_count

    @property
    def drop_fraction(self) -> float | None:
        return drop_fraction(self.previous_count, self.horizon_count)

    def has(self, code: str) -> bool:
        """Did this gate fire? The readable form of ``code in verdict.codes``."""
        return code in self.codes

    def as_dict(self) -> dict[str, Any]:
        fraction = self.drop_fraction
        return {
            "space_id": self.space_id,
            "label": self.label,
            "key": self.key,
            "status": self.status,
            "outcome": self.outcome.value,
            "blocked": self.blocked,
            "reasons": [reason.as_dict() for reason in self.reasons],
            "codes": list(self.codes),
            "alerts": list(self.alerts),
            "overrides": list(self.overrides),
            "horizon_count": self.horizon_count,
            "event_count": self.event_count,
            "previous_count": self.previous_count,
            "delta": self.delta,
            "drop_fraction": round(fraction, 4) if fraction is not None else None,
            "stale_days": round(self.stale_days, 2) if self.stale_days is not None else None,
            "max_stale_days": self.max_stale_days,
            "ever_nonzero": self.ever_nonzero,
            "carried_forward": self.carried_forward,
            "reused_unchanged": self.reused_unchanged,
            "escalated": self.escalated,
        }


@dataclass(frozen=True)
class HealthVerdict:
    """**Whether publication proceeds**, with the reasons at both levels.

    The CLI reads :attr:`blocks_publication` and :attr:`reasons`; issue 0017
    writes :meth:`as_dict` into ``health.json``; issue 0032 alerts on
    :attr:`alerts`. It is deliberately a value object with no behavior beyond
    reporting itself — the decision is made once, in :func:`evaluate_health`, and
    everything downstream reads the same answer.

    :attr:`reasons` is the flat list of *blocking* reasons, which is the field
    :meth:`~pipeline.store.Store.finish_run` persists and the CLI turns into
    ``publish_skipped_reason``. Everything non-blocking lives in :attr:`notes`.
    """

    blocked: bool = False
    #: Blocking reasons only, global first. What the CLI prints and the store keeps.
    reasons: tuple[str, ...] = ()
    #: Non-blocking observations worth reporting. Never a reason to stop.
    notes: tuple[str, ...] = ()
    #: Everything issue 0032 should tell a human about, blocking or not.
    alerts: tuple[str, ...] = ()

    global_reasons: tuple[GateReason, ...] = ()
    sources: tuple[SourceVerdict, ...] = ()

    event_count: int = 0
    previous_event_count: int | None = None
    previous_run_id: int | None = None
    run_id: int | None = None
    space_filter: str | None = None
    checked_at: dt.datetime | None = None

    #: What :func:`audit_events` removed at publish scope. Not the same numbers
    #: as ``record.dropped_count`` / ``record.quarantined_count``, which are
    #: normalize's, at source scope. See the module docstring.
    dropped_count: int = 0
    quarantined_count: int = 0
    audit: EventAudit | None = None

    #: The publishable set after the audit. Excluded from :meth:`as_dict` —
    #: ``health.json`` reports counts, not the calendar.
    events: tuple[Event, ...] = field(default=(), repr=False)

    thresholds: Mapping[str, float] = field(
        default_factory=lambda: {
            "global_drop": GLOBAL_DROP_THRESHOLD,
            "source_drop": SOURCE_DROP_THRESHOLD,
        }
    )
    gate_issue: str = GATE_ISSUE
    implemented: bool = True

    # -- the question the CLI asks --------------------------------------------

    @property
    def blocks_publication(self) -> bool:
        return self.blocked

    @property
    def drop_fraction(self) -> float | None:
        return drop_fraction(self.previous_event_count, self.event_count)

    @property
    def blocked_sources(self) -> tuple[SourceVerdict, ...]:
        return tuple(verdict for verdict in self.sources if verdict.blocked)

    @property
    def degraded_sources(self) -> tuple[SourceVerdict, ...]:
        return tuple(
            verdict for verdict in self.sources if verdict.outcome is GateOutcome.DEGRADED
        )

    @property
    def exempt_sources(self) -> tuple[SourceVerdict, ...]:
        return tuple(
            verdict for verdict in self.sources if verdict.outcome is GateOutcome.EXEMPT
        )

    def source(self, space_id: str, label: str) -> SourceVerdict | None:
        for verdict in self.sources:
            if verdict.space_id == space_id and verdict.label == label:
                return verdict
        return None

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready. Issue 0017 writes exactly this into ``health.json``."""
        fraction = self.drop_fraction
        return {
            "blocked": self.blocked,
            "reasons": list(self.reasons),
            "notes": list(self.notes),
            "alerts": list(self.alerts),
            "gate_issue": self.gate_issue,
            "implemented": self.implemented,
            "checked_at": self.checked_at.isoformat() if self.checked_at else None,
            "run_id": self.run_id,
            "previous_run_id": self.previous_run_id,
            "space_filter": self.space_filter,
            "event_count": self.event_count,
            "previous_event_count": self.previous_event_count,
            "drop_fraction": round(fraction, 4) if fraction is not None else None,
            "dropped_count": self.dropped_count,
            "quarantined_count": self.quarantined_count,
            "thresholds": dict(self.thresholds),
            "global_reasons": [reason.as_dict() for reason in self.global_reasons],
            "audit": self.audit.as_dict() if self.audit else None,
            "sources": [verdict.as_dict() for verdict in self.sources],
            "counts": {
                "sources": len(self.sources),
                "blocked": len(self.blocked_sources),
                "degraded": len(self.degraded_sources),
                "exempt": len(self.exempt_sources),
            },
        }

    def line(self) -> str:
        """One line for the run report."""
        if self.blocked:
            return f"BLOCKED — {'; '.join(self.reasons)}"
        counts = (
            f"{len(self.sources)} sources checked, "
            f"{len(self.degraded_sources)} degraded, {len(self.exempt_sources)} exempt"
        )
        fraction = self.drop_fraction
        if fraction is not None:
            counts += f", night-over-night {self.previous_event_count} -> {self.event_count}"
        return f"passed — {counts}"

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        return self.line()


# --------------------------------------------------------------------------- per source


def evaluate_source(
    record: Any,
    *,
    health: Health | None = None,
    previous_count: int | None = None,
    ever_nonzero: bool = False,
) -> SourceVerdict:
    """Gate one source. Pure: every input is an argument, no store, no clock.

    ``record`` is a :class:`pipeline.cli.SourceRecord`, taken structurally for
    the same reason :mod:`pipeline.store` and :mod:`pipeline.carry_forward` do —
    this module sits underneath the CLI and an import would be a cycle.
    """
    config = health or Health()
    space_id = str(getattr(record, "space_id", "") or "")
    label = str(getattr(record, "label", "") or "")
    status = str(getattr(record, "status", "") or "")
    horizon_count = int(getattr(record, "horizon_count", 0) or 0)
    event_count = int(getattr(record, "event_count", 0) or 0)
    stale_days = getattr(record, "stale_days", None)
    carried_forward = bool(getattr(record, "carried_forward", False))
    reused_unchanged = bool(getattr(record, "reused_unchanged", False))
    escalated = bool(getattr(record, "escalated", False))

    base = SourceVerdict(
        space_id=space_id,
        label=label,
        status=status,
        horizon_count=horizon_count,
        event_count=event_count,
        previous_count=previous_count,
        stale_days=stale_days,
        max_stale_days=config.max_stale_days,
        ever_nonzero=ever_nonzero,
        carried_forward=carried_forward,
        reused_unchanged=reused_unchanged,
        escalated=escalated,
    )

    def verdict(
        outcome: GateOutcome,
        reasons: Sequence[GateReason] = (),
        overrides: Sequence[str] = (),
    ) -> SourceVerdict:
        return replace(
            base, outcome=outcome, reasons=tuple(reasons), overrides=tuple(overrides)
        )

    # -- out of scope ---------------------------------------------------------

    if status == "skipped":
        because = getattr(record, "skipped_because", None) or "skipped"
        return verdict(
            GateOutcome.NOT_EVALUATED,
            [
                GateReason(
                    "not_in_run_set",
                    f"{space_id}:{label} was not fetched ({because}); it contributes "
                    "no events by decision, so no gate applies",
                )
            ],
        )

    if status == "blocked":
        # robots.txt said no and we honored it. Documented policy: never blocks,
        # never alerts. See the module docstring.
        return verdict(
            GateOutcome.EXEMPT,
            [
                GateReason(
                    "blocked_by_robots",
                    f"{space_id}:{label} is disallowed by robots.txt and was not "
                    "fetched. That is the site's decision correctly honored, not a "
                    "failure of ours, so it neither blocks publication nor alerts",
                )
            ],
        )

    # -- 304: current by definition -------------------------------------------

    if reused_unchanged:
        return verdict(
            GateOutcome.OK,
            [
                GateReason(
                    "reused_unchanged",
                    f"{space_id}:{label} answered HTTP 304 and replayed "
                    f"{horizon_count} stored events. Nothing changed is what a 304 "
                    "means, so it is neither a zero nor stale",
                )
            ],
        )

    # -- the source failed: carry-forward owns this ---------------------------

    if status in FAILED_STATUSES or carried_forward or escalated:
        reasons = [
            GateReason(
                "escalated" if escalated else "carried_forward",
                (
                    f"{space_id}:{label} has failed enough nights that carry-forward "
                    "stopped; its events are withheld and the global gate will see "
                    "the shortfall"
                )
                if escalated
                else (
                    f"{space_id}:{label} failed and republished its last-known-good "
                    "events. Already degraded and already reported; blocking here "
                    "would delete ten other spaces to punish one transient failure"
                ),
            )
        ]
        return verdict(GateOutcome.DEGRADED, reasons)

    # -- the real gates -------------------------------------------------------

    reasons: list[GateReason] = []
    overrides: list[str] = []
    outcome = GateOutcome.OK

    # 1. staleness. The Noisebridge case: a healthy, constant event count from a
    #    calendar nothing has touched since January 2024, because five RRULEs
    #    have no UNTIL. Every count-based gate says fine; this one does not.
    if config.max_stale_days is not None:
        if stale_days is None:
            reasons.append(
                GateReason(
                    "no_last_modified",
                    f"{space_id}:{label} has max_stale_days={config.max_stale_days} "
                    "but the feed dated nothing at all (no LAST-MODIFIED, no "
                    "DTSTAMP), so staleness cannot be judged",
                    alert=True,
                )
            )
        elif stale_days > config.max_stale_days:
            outcome = GateOutcome.BLOCKED
            reasons.append(
                GateReason(
                    "stale_feed",
                    f"{space_id}:{label} last changed {stale_days:.0f} days ago, "
                    f"over its max_stale_days of {config.max_stale_days}, while "
                    f"still producing {horizon_count} events. An abandoned feed "
                    "with unbounded RRULEs generates plausible events forever at a "
                    "constant rate and passes every count-based check — this is "
                    "fabricated data, not a calendar",
                    blocking=True,
                    alert=True,
                )
            )

    # 2. zero. The metric is horizon_count: post-expansion, inside the horizon.
    #    89 VEVENTs for ~7 live events means raw_count answers a different
    #    question, and this module never reads it.
    if horizon_count == 0:
        if config.allow_zero:
            overrides.append("allow_zero")
            reasons.append(
                GateReason(
                    "allow_zero",
                    f"{space_id}:{label} returned 0 events and is marked allow_zero "
                    "(low volume by nature). Alerting monthly on this teaches "
                    "everyone to ignore alerts",
                )
            )
            outcome = GateOutcome.EXEMPT if outcome is GateOutcome.OK else outcome
        elif config.require_nonzero_once and not ever_nonzero:
            overrides.append("require_nonzero_once")
            reasons.append(
                GateReason(
                    "require_nonzero_once",
                    f"{space_id}:{label} has never returned an event, so it cannot "
                    "have gone to zero. Waiting, not failing — the gate starts "
                    "applying the moment it produces one",
                )
            )
            outcome = GateOutcome.EXEMPT if outcome is GateOutcome.OK else outcome
        elif previous_count is not None and previous_count > 0:
            outcome = GateOutcome.BLOCKED
            reasons.append(
                GateReason(
                    "zero_after_nonzero",
                    f"{space_id}:{label} returned 0 events after {previous_count} "
                    "on its last run. A feed does not empty overnight; this is a "
                    "source that changed or an adapter that broke",
                    blocking=True,
                    alert=True,
                )
            )
        elif previous_count is None:
            reasons.append(
                GateReason(
                    "zero_no_baseline",
                    f"{space_id}:{label} returned 0 events and has no prior run to "
                    "compare against. A first night is not a decline",
                )
            )
        else:
            reasons.append(
                GateReason(
                    "zero_again",
                    f"{space_id}:{label} returned 0 events, as it did last run. "
                    "Nothing changed tonight, so there is nothing new to block for",
                )
            )
    else:
        # 3. a large drop that is not a zero. Alert-only, always: Maker Nexus
        #    posts 4-8 weeks ahead, so the far end of a 120-day window moves
        #    legitimately, and blocking on that would be the "short publishing
        #    horizon reads as a decline" mistake.
        fraction = drop_fraction(previous_count, horizon_count)
        if config.ignore_count_drop:
            overrides.append("ignore_count_drop")
            if fraction is not None and fraction > SOURCE_DROP_THRESHOLD:
                reasons.append(
                    GateReason(
                        "ignore_count_drop",
                        f"{space_id}:{label} went {previous_count} -> "
                        f"{horizon_count}, ignored: the feed is capped or "
                        "truncated, so a nightly delta is noise rather than news",
                    )
                )
                outcome = GateOutcome.EXEMPT if outcome is GateOutcome.OK else outcome
        elif fraction is not None and fraction > SOURCE_DROP_THRESHOLD:
            reasons.append(
                GateReason(
                    "count_drop",
                    f"{space_id}:{label} went {previous_count} -> {horizon_count} "
                    f"({fraction:.0%} down) night over night. Not blocking on its "
                    "own — one feed's publishing horizon breathes — but worth a "
                    "look",
                    alert=True,
                )
            )
            outcome = GateOutcome.DEGRADED if outcome is GateOutcome.OK else outcome

        # A source whose filters removed everything is publishing nothing while
        # looking healthy on the gate metric. Said out loud, never blocking:
        # ``title_contains`` doing its job is indistinguishable from here.
        if event_count == 0:
            reasons.append(
                GateReason(
                    "filtered_to_zero",
                    f"{space_id}:{label} produced {horizon_count} events in the "
                    "horizon and the registry's filters removed all of them",
                )
            )

    if outcome is GateOutcome.BLOCKED:
        return verdict(outcome, reasons, overrides)
    if any(reason.blocking for reason in reasons):  # pragma: no cover - defensive
        return verdict(GateOutcome.BLOCKED, reasons, overrides)
    return verdict(outcome, reasons, overrides)


# --------------------------------------------------------------------------- the gate


def evaluate_health(
    records: Sequence[Any],
    *,
    events: Sequence[Event] = (),
    registry: Registry | None = None,
    store: Any | None = None,
    now: dt.datetime | None = None,
    run_id: int | None = None,
    space_filter: str | None = None,
    timezone: str | None = None,
) -> HealthVerdict:
    """Decide whether this run may publish. **Called before anything is written.**

    Every argument after ``records`` is optional so the seam stays callable from
    a test with nothing but a list of records — but the night-over-night gates
    need ``store``, and without it they report "no baseline" rather than
    pretending to have checked.

    The order matters: the event audit runs first, so the totals the global gate
    compares are the totals that would actually be published.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    tz = timezone or (registry.timezone if registry is not None else "America/Los_Angeles")
    index = health_index(registry)

    # -- publish-scope event audit --------------------------------------------
    audit = audit_events(events, now=now, timezone=tz)
    if not audit.clean:
        LOG.warning(
            "health audit removed %d events at publish scope (%d outside the "
            "sanity window, %d quarantined). normalize (issue 0009) should have "
            "caught these first — something reached the gate without passing "
            "through it.",
            audit.dropped_count + audit.quarantined_count,
            audit.dropped_count,
            audit.quarantined_count,
        )
        for uid, reason, detail in audit.dropped:
            LOG.warning("dropped %s: %s (%s)", uid, reason.value, detail)
        for uid, reason in audit.quarantined:
            LOG.warning("quarantined %s: %s", uid, reason.value)

    # -- per source ------------------------------------------------------------
    source_verdicts: list[SourceVerdict] = []
    for record in records:
        space_id = str(getattr(record, "space_id", "") or "")
        label = str(getattr(record, "label", "") or "")
        key = f"{space_id}:{label}"
        previous = (
            previous_source_count(store, space_id, label, run_id=run_id)
            if store is not None
            else None
        )
        source_verdicts.append(
            evaluate_source(
                record,
                health=index.get(key),
                previous_count=previous,
                ever_nonzero=(
                    _ever_nonzero(store, space_id, label) if store is not None else False
                ),
            )
        )

    # -- global ----------------------------------------------------------------
    event_count = len(audit.events)
    previous_event_count: int | None = None
    previous_run_id: int | None = None
    global_reasons: list[GateReason] = []

    if space_filter:
        global_reasons.append(
            GateReason(
                "global_gate_skipped",
                f"the global count gate does not run for a --space {space_filter} "
                "run: its event set is one space's worth by construction, so any "
                "comparison against a full run reports a collapse that did not "
                "happen. A single-space run publishes nothing anyway",
            )
        )
    elif store is not None:
        previous_event_count, previous_run_id = previous_total(store, run_id=run_id)
        fraction = drop_fraction(previous_event_count, event_count)
        if fraction is None:
            global_reasons.append(
                GateReason(
                    "global_no_baseline",
                    "no comparable previous run, so there is no night-over-night "
                    "total to gate on",
                )
            )
        elif fraction > GLOBAL_DROP_THRESHOLD:
            global_reasons.append(
                GateReason(
                    "global_count_drop",
                    f"the calendar dropped {fraction:.0%} night over night "
                    f"({previous_event_count} -> {event_count} events), over the "
                    f"{GLOBAL_DROP_THRESHOLD:.0%} threshold. Publishing that would "
                    "delete events from every subscriber's calendar on the strength "
                    "of one bad night",
                    blocking=True,
                    alert=True,
                )
            )
        else:
            global_reasons.append(
                GateReason(
                    "global_ok",
                    f"night-over-night total {previous_event_count} -> "
                    f"{event_count} ({fraction:.0%} down), within the "
                    f"{GLOBAL_DROP_THRESHOLD:.0%} threshold",
                )
            )

    # -- assemble --------------------------------------------------------------
    blocking = list(_messages(global_reasons, blocking=True))
    notes = list(_messages(global_reasons, blocking=False))
    alerts = [reason.message for reason in global_reasons if reason.alert]
    for verdict in source_verdicts:
        blocking.extend(verdict.blocking_reasons)
        notes.extend(_messages(verdict.reasons, blocking=False))
        alerts.extend(verdict.alerts)

    result = HealthVerdict(
        blocked=bool(blocking),
        reasons=tuple(blocking),
        notes=tuple(notes),
        alerts=tuple(alerts),
        global_reasons=tuple(global_reasons),
        sources=tuple(source_verdicts),
        event_count=event_count,
        previous_event_count=previous_event_count,
        previous_run_id=previous_run_id,
        run_id=run_id,
        space_filter=space_filter,
        checked_at=now,
        dropped_count=audit.dropped_count,
        quarantined_count=audit.quarantined_count,
        audit=audit,
        events=audit.events,
    )
    if result.blocked:
        LOG.error("health gates blocked publication: %s", "; ".join(result.reasons))
    return result


__all__ = [
    "GATE_ISSUE",
    "GLOBAL_DROP_THRESHOLD",
    "RUN_HISTORY_LIMIT",
    "SOURCE_DROP_THRESHOLD",
    "SOURCE_HISTORY_LIMIT",
    "EventAudit",
    "GateOutcome",
    "GateReason",
    "HealthVerdict",
    "SourceVerdict",
    "audit_events",
    "drop_fraction",
    "evaluate_health",
    "evaluate_source",
    "health_index",
    "merge_health",
    "previous_source_count",
    "previous_total",
]
