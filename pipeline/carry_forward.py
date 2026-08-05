"""Carry-forward: a failed source republishes its last-known-good events.

CLAUDE.md, in the invariants: *"A failed source carries forward its previous
events. A transient 503 must never silently delete a space from the calendar."*
This module is that sentence.

The shape of the decision
--------------------------------------------------------------------------

Five transport outcomes reach the run loop, and they do **not** all mean the
same thing:

============== ================================================================
``failed``     Carry forward. We tried, we were allowed to try, and the network
               or the server or our own parser lost. Yesterday's events are the
               best truth available and dropping them deletes a space.
``error``      Carry forward. An unhandled exception in *our* code on *one*
               feed is a bug in us, not evidence that the space cancelled
               everything. Issue 0012's run loop already said as much in a
               comment; this is that comment made executable.
``blocked``    **Never.** ``robots.txt`` forbade the fetch. Republishing a
               stale copy is working around the file by another route, and
               CLAUDE.md is explicit: a disallow means we do not fetch it, not
               that we find an equivalent route. Issue 0006's agent flagged
               this and was right. The space drops out, visibly.
``skipped``    Never. ``enabled: false`` or a ``TODO`` url is a *decision*, and
               reviving events from a source somebody deliberately switched off
               would make the registry a suggestion.
``not_modified`` Not carry-forward at all — see below.
============== ================================================================

A 304 is not a failure and is not stale
--------------------------------------------------------------------------

Wiring the SQLite store into the CLI (the other half of issue 0014) turns
conditional GET on for the first time, which means ``304 Not Modified`` starts
happening nightly — and a 304 carries no body, so the adapter returns zero
events. Left alone, switching conditional GET on would have quietly deleted
every unchanged space from the calendar: the exact failure this project keeps
designing against, introduced by an optimization.

So a 304 **replays** the stored event set too, but it is a different thing from
carry-forward and is labelled differently (:attr:`CarryReason.NOT_MODIFIED`,
``reused_unchanged`` on the record). The server affirmed the bytes are current.
Nothing is stale, no failure counter moves, nothing escalates, and
``health.json`` must not show it as degraded. The ICS adapter already says so in
the error string it returns for a 304: *"reuse the stored events rather than
treating this as zero"*.

Escalation: three nights, then stop
--------------------------------------------------------------------------

Carry-forward is a bridge over a transient failure, not a preservative. After
:data:`ESCALATE_AFTER_NIGHTS` consecutive failed nights — 3, matching the
OpenCode repair trigger in CLAUDE.md — the source stops carrying forward and is
marked for alerting instead. By that point the data is at least three days old
and the horizon has been eroding it every night anyway; what is left is a ghost
calendar, and publishing ghosts is its own kind of silent damage.

The invariant survives this, because it is about *silence*: a 503 must never
**silently** delete a space. Nights one and two are silent and carried. Night
three is loud — :attr:`CarryForward.alert`, ``escalated`` on the record, and
material for issue 0016's gates and issue 0017's ``health.json``.

The horizon still applies
--------------------------------------------------------------------------

Republished events go through the same window the adapter would have clipped
them to: nothing that started before today, nothing past ``horizon_days``. A
carried-forward calendar therefore *decays* rather than freezing — last night's
event that has since happened drops out on its own, and a source that stays
broken shrinks toward empty instead of advertising a class that ended a week
ago.

Issue 0014.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

from pipeline.normalize import Event, day_start_utc

# --------------------------------------------------------------------------- knobs

#: Record statuses that republish the last-known-good set. See the table above.
CARRY_FORWARD_STATUSES = frozenset({"failed", "error"})

#: Statuses that must **never** republish, spelled out rather than left to the
#: absence of a branch. ``blocked`` is the load-bearing one.
NEVER_CARRY_FORWARD = frozenset({"blocked", "skipped"})

#: A 304 replays the same rows, under a different name and with no staleness.
REPLAY_STATUS = "not_modified"

#: Consecutive failed nights before carry-forward stops and alerting starts.
#: Three, matching the repair trigger in CLAUDE.md.
ESCALATE_AFTER_NIGHTS = 3

#: How far back :func:`consecutive_failures` will scan the run history.
FAILURE_SCAN_LIMIT = 60


# --------------------------------------------------------------------------- result


class CarryReason(str, Enum):
    """Why stored events were republished. Two different situations."""

    #: The source failed. The data is aged and the age is reported.
    FAILED = "failed"
    #: HTTP 304. The data is current; the server said so.
    NOT_MODIFIED = "not_modified"


@dataclass(frozen=True)
class CarryForward:
    """What carry-forward decided for one source, as data.

    Returned even when nothing was republished (``available`` false), because
    "there was nothing to carry forward" is a fact ``health.json`` wants and an
    absent object is not.
    """

    space_id: str
    label: str
    reason: CarryReason
    events: tuple[Event, ...] = ()
    #: UIDs that were in the stored set and have since left the horizon.
    dropped_uids: tuple[str, ...] = ()
    last_known_good_at: dt.datetime | None = None
    #: Seconds between ``last_known_good_at`` and now. ``None`` when unknown.
    age_seconds: float | None = None
    consecutive_failures: int = 0
    escalated: bool = False
    alert: str | None = None
    note: str = ""

    @property
    def key(self) -> str:
        return f"{self.space_id}:{self.label}"

    @property
    def available(self) -> bool:
        """True when events were actually republished."""
        return bool(self.events)

    @property
    def count(self) -> int:
        return len(self.events)

    @property
    def dropped_count(self) -> int:
        return len(self.dropped_uids)

    @property
    def age_days(self) -> float | None:
        if self.age_seconds is None:
            return None
        return self.age_seconds / 86400.0

    @property
    def carried_forward(self) -> bool:
        """True only for a *failure*. A 304 is not carry-forward."""
        return self.reason is CarryReason.FAILED and self.available

    @property
    def reused_unchanged(self) -> bool:
        """True for the 304 replay. Not degraded, not stale, not an alert."""
        return self.reason is CarryReason.NOT_MODIFIED and self.available

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready. Issue 0017 surfaces exactly this in ``health.json``."""
        return {
            "space_id": self.space_id,
            "label": self.label,
            "reason": self.reason.value,
            "carried_forward": self.carried_forward,
            "reused_unchanged": self.reused_unchanged,
            "count": self.count,
            "dropped_count": self.dropped_count,
            "dropped_uids": list(self.dropped_uids),
            "last_known_good_at": (
                self.last_known_good_at.isoformat() if self.last_known_good_at else None
            ),
            "age_seconds": round(self.age_seconds, 3) if self.age_seconds is not None else None,
            "age_days": round(self.age_days, 3) if self.age_days is not None else None,
            "consecutive_failures": self.consecutive_failures,
            "escalated": self.escalated,
            "alert": self.alert,
            "note": self.note,
        }

    def line(self) -> str:  # pragma: no cover - diagnostics only
        if self.escalated:
            return f"{self.key}: {self.alert}"
        if not self.available:
            return f"{self.key}: nothing carried forward — {self.note}"
        age = f"{self.age_days:.1f}d" if self.age_days is not None else "unknown age"
        verb = "reused" if self.reused_unchanged else "carried forward"
        return (
            f"{self.key}: {verb} {self.count} events ({age} old, "
            f"{self.dropped_count} out of horizon)"
        )


# --------------------------------------------------------------------------- horizon


def _zone(tz: str) -> dt.tzinfo:
    """The event's zone, falling back to UTC rather than raising.

    A stored event was normalized before it was written, so its ``tz`` is
    already known-good; this only guards against a row written by a build that
    knew a zone name this one does not.
    """
    try:
        return ZoneInfo(tz)
    except Exception:  # pragma: no cover - defensive
        return dt.timezone.utc


def horizon_window(
    now: dt.datetime, horizon_days: int, tz: str
) -> tuple[dt.datetime, dt.datetime]:
    """``(floor, ceiling)`` in UTC for *tz*, matching the adapter's window.

    The floor is the start of **today** in the source's zone, not ``now``: the
    nightly run starts at 03:15 and an event at 10:00 this morning is still an
    event. The ceiling is ``today + horizon_days``, the same bound
    :func:`pipeline.adapters.ics.parse_ics_text` expands between.
    """
    today = now.astimezone(_zone(tz)).date()
    return (
        day_start_utc(today, tz),
        day_start_utc(today + dt.timedelta(days=max(horizon_days, 0)), tz),
    )


def within_horizon(event: Event, *, now: dt.datetime, horizon_days: int) -> bool:
    """Would tonight's adapter still have produced this event?

    All-day events are current for the whole of their last day — the same rule
    :mod:`pipeline.normalize` applies — so a two-day festival that began
    yesterday is still on tonight's calendar.
    """
    floor, ceiling = horizon_window(now, horizon_days, event.tz)
    if event.all_day and event.end_date is not None:
        last = day_start_utc(event.end_date + dt.timedelta(days=1), event.tz)
        if last <= floor:
            return False
    elif event.start_utc < floor:
        return False
    return event.start_utc <= ceiling


def clip_to_horizon(
    events: list[Event] | tuple[Event, ...],
    *,
    now: dt.datetime,
    horizon_days: int,
) -> tuple[list[Event], list[str]]:
    """``(kept, dropped_uids)``. Yesterday's past events really do fall out."""
    kept: list[Event] = []
    dropped: list[str] = []
    for event in events:
        if within_horizon(event, now=now, horizon_days=horizon_days):
            kept.append(event)
        else:
            dropped.append(event.uid)
    return kept, dropped


# --------------------------------------------------------------------------- failures


def consecutive_failures(store: Any, space_id: str, label: str) -> int:
    """How many nights in a row this source has failed, **including tonight**.

    Two sources of truth, and the larger wins, because neither one sees
    everything:

    - :class:`~pipeline.fetch.SourceState.consecutive_failures`, which the fetch
      layer increments on a transport failure — but which a *parse* failure
      resets to zero, since the transport succeeded. A feed answering 200 with
      the homepage every night would never escalate on this counter alone.
    - The recorded run history, which counts ``failed``/``error`` rows from the
      newest non-dry run backwards and therefore catches the drifted-adapter
      case that matters most.

    The history count excludes tonight (this run is not written yet), so it is
    incremented by one. Dry runs are excluded from both — see the dry-run policy
    in :mod:`pipeline.cli`.
    """
    prior = 0
    counter = getattr(store, "consecutive_failed_runs", None)
    if callable(counter):
        prior = int(counter(space_id, label, statuses=tuple(CARRY_FORWARD_STATUSES)))

    state_failures = 0
    getter = getattr(store, "get", None)
    if callable(getter):
        state = getter(f"{space_id}:{label}")
        state_failures = int(getattr(state, "consecutive_failures", 0) or 0)

    return max(prior + 1, state_failures)


# --------------------------------------------------------------------------- the plan


def plan_carry_forward(
    store: Any,
    space_id: str,
    label: str,
    *,
    status: str,
    now: dt.datetime,
    horizon_days: int,
    escalate_after: int = ESCALATE_AFTER_NIGHTS,
) -> CarryForward | None:
    """Decide what to republish for one source. ``None`` when the status is not one.

    ``store`` is taken structurally, exactly as :mod:`pipeline.store` takes a
    ``SourceRecord``, so this module sits underneath both and no import cycle is
    possible.
    """
    if status == REPLAY_STATUS:
        reason = CarryReason.NOT_MODIFIED
    elif status in CARRY_FORWARD_STATUSES:
        reason = CarryReason.FAILED
    else:
        # blocked and skipped land here, and so does a healthy run. Spelled as
        # a return rather than an omission: see NEVER_CARRY_FORWARD.
        return None

    last_good = store.last_known_good_at(space_id, label)
    age = (now - last_good).total_seconds() if last_good is not None else None
    failures = (
        consecutive_failures(store, space_id, label)
        if reason is CarryReason.FAILED
        else 0
    )

    base = CarryForward(
        space_id=space_id,
        label=label,
        reason=reason,
        last_known_good_at=last_good,
        age_seconds=age,
        consecutive_failures=failures,
    )

    stored = store.carry_forward_events(space_id, label)
    if not stored:
        return replace(
            base,
            note=(
                "no last-known-good events are stored for this source; there is "
                "nothing to carry forward"
            ),
        )

    if reason is CarryReason.FAILED and failures >= escalate_after:
        days = f"{age / 86400.0:.1f} days" if age is not None else "an unknown age"
        return replace(
            base,
            escalated=True,
            note="carry-forward stopped: this is no longer a transient failure",
            alert=(
                f"{space_id}:{label} has failed {failures} nights running. "
                f"Carry-forward stopped rather than republishing data {days} old — "
                f"{len(stored)} events withheld. Diff raw/ across those nights and "
                "run the repair workflow (CLAUDE.md)."
            ),
        )

    kept, dropped = clip_to_horizon(stored, now=now, horizon_days=horizon_days)
    note = (
        "republished the last-known-good set"
        if reason is CarryReason.FAILED
        else "HTTP 304: reused the stored set, which the server says is current"
    )
    if dropped:
        note += f"; {len(dropped)} events have since left the {horizon_days}-day horizon"
    return replace(base, events=tuple(kept), dropped_uids=tuple(dropped), note=note)


def apply_carry_forward(
    record: Any,
    store: Any,
    *,
    now: dt.datetime,
    horizon_days: int,
    escalate_after: int = ESCALATE_AFTER_NIGHTS,
) -> CarryForward | None:
    """Plan carry-forward for ``record`` and stamp the outcome onto it.

    ``record`` is a :class:`pipeline.cli.SourceRecord`, taken structurally. The
    fields written here are the ones issue 0017 reads: the flag, the age, the
    failure count and the alert.
    """
    plan = plan_carry_forward(
        store,
        getattr(record, "space_id", ""),
        getattr(record, "label", ""),
        status=getattr(record, "status", ""),
        now=now,
        horizon_days=horizon_days,
        escalate_after=escalate_after,
    )
    if plan is None:
        return None

    record.carried_forward = plan.carried_forward
    record.reused_unchanged = plan.reused_unchanged
    record.carry_forward_count = plan.count
    record.carry_forward_dropped_count = plan.dropped_count
    record.carry_forward_age_seconds = plan.age_seconds
    record.last_known_good_at = (
        plan.last_known_good_at.isoformat() if plan.last_known_good_at else None
    )
    record.consecutive_failures = plan.consecutive_failures
    record.escalated = plan.escalated
    record.alert = plan.alert
    record.carry_forward_note = plan.note

    if plan.reused_unchanged:
        # A 304 is a healthy source that contributed ``count`` events. Leaving
        # these at zero would hand issue 0016 a "went to zero" that never
        # happened, on the one outcome that is *defined* as nothing changing.
        record.event_count = plan.count
        record.horizon_count = plan.count
    # A *failure* deliberately leaves ``event_count`` at zero. The events reach
    # the calendar, but the source produced none tonight, and a gate reading
    # ``event_count`` must see the failure rather than the bridge over it.
    # ``carry_forward_count`` is where the republished number lives.
    return plan


__all__ = [
    "CARRY_FORWARD_STATUSES",
    "ESCALATE_AFTER_NIGHTS",
    "FAILURE_SCAN_LIMIT",
    "NEVER_CARRY_FORWARD",
    "REPLAY_STATUS",
    "CarryForward",
    "CarryReason",
    "apply_carry_forward",
    "clip_to_horizon",
    "consecutive_failures",
    "horizon_window",
    "plan_carry_forward",
    "within_horizon",
]
