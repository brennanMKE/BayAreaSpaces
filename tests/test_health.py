"""Tests for the publish gates (issue 0016).

**This is the module that decides whether the project publishes fiction**, so
the tests are written around the failures rather than around the functions.
Every case below is a real source in ``sources.yaml``, named in the test:

- Noisebridge's retired gCal, which emits a *perfectly constant* number of
  plausible events forever and passes every count-based check ever written.
  ``test_a_stale_feed_is_caught_even_though_every_count_is_perfect`` is the one
  to read first — it is the case the original design missed.
- The Box Shop and Lower 48, which are empty for months at a time.
- Hacker Dojo's Meetup iCal, capped at 10 with no pagination.
- Humanmade's Eventbrite organizer and Hacker Dojo's Luma page, which have never
  been non-zero and so can never "go to zero".

Plus the two counting rules — gate on ``horizon_count``, never ``raw_count``;
a short publishing horizon is not a decline — and the three interactions with
what already exists: a 304 is not a zero and is not stale, a carried-forward
source is already degraded, and a ``blocked`` source is not our failure.

No test here touches the network. The CLI case runs two nights through
``httpx.MockTransport`` against one temporary SQLite store, which is the only
way to test a night-over-night gate honestly.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import httpx
import pytest

from pipeline.carry_forward import apply_carry_forward
from pipeline.cli import (
    EXIT_HEALTH_BLOCKED,
    EXIT_OK,
    STAGING_DIRNAME,
    SourceRecord,
    main,
)
from pipeline.config import CONTACT_ENV_VAR, Health, load_registry
from pipeline.health import (
    GLOBAL_DROP_THRESHOLD,
    SOURCE_DROP_THRESHOLD,
    GateOutcome,
    HealthVerdict,
    audit_events,
    drop_fraction,
    evaluate_health,
    evaluate_source,
    health_index,
    merge_health,
    previous_source_count,
    previous_total,
)
from pipeline.normalize import Event
from pipeline.store import Store

TEST_CONTACT = "https://maker-calendar.test/about"
NOW = dt.datetime(2026, 8, 5, 3, 15, 0, tzinfo=dt.timezone.utc)


# --------------------------------------------------------------------------- harness


def make_record(
    space_id: str = "ace",
    label: str = "ics",
    *,
    status: str = "ok",
    horizon_count: int = 0,
    event_count: int | None = None,
    **kwargs,
) -> SourceRecord:
    """A :class:`SourceRecord` with only the fields the gates read."""
    return SourceRecord(
        space_id=space_id,
        label=label,
        adapter="ics",
        status=status,
        horizon_count=horizon_count,
        event_count=horizon_count if event_count is None else event_count,
        **kwargs,
    )


def event(
    uid: str = "e1",
    *,
    title: str = "Open Shop Night",
    start: dt.datetime | None = None,
    space_id: str = "ace",
) -> Event:
    return Event(
        uid=uid,
        space_id=space_id,
        source_label="ics",
        title=title,
        start_utc=start or (NOW + dt.timedelta(days=7)),
        end_utc=None,
        tz="America/Los_Angeles",
    )


@pytest.fixture
def env() -> dict[str, str]:
    return {CONTACT_ENV_VAR: TEST_CONTACT}


@pytest.fixture
def registry(env: dict[str, str]):
    return load_registry(env=env)


@pytest.fixture
def store() -> Store:
    with Store.in_memory() as db:
        yield db


def night(
    store: Store,
    records: list[SourceRecord],
    *,
    total: int | None = None,
    when: dt.datetime | None = None,
) -> int:
    """Record one completed run so the next one has a baseline to compare to."""
    run_id = store.start_run(when or NOW, horizon_days=120)
    for record in records:
        store.record_source_run(run_id, record)
    store.finish_run(
        run_id,
        finished_at=when or NOW,
        event_count=total if total is not None else sum(r.event_count for r in records),
        published=True,
    )
    return run_id


# --------------------------------------------------------------------------- per source: zero


def test_a_source_going_from_twelve_to_zero_blocks_publication():
    """The baseline gate. A feed does not empty overnight."""
    verdict = evaluate_source(make_record(horizon_count=0), previous_count=12)

    assert verdict.blocked is True
    assert verdict.outcome is GateOutcome.BLOCKED
    assert verdict.has("zero_after_nonzero")
    assert verdict.previous_count == 12 and verdict.horizon_count == 0
    assert verdict.alerts, "a blocked source must also alert"
    assert "12" in verdict.blocking_reasons[0]


def test_allow_zero_lets_the_box_shop_be_empty_without_blocking():
    """~8-12 events a year. Without this it alerts monthly and gets ignored."""
    verdict = evaluate_source(
        make_record(space_id="the-box-shop", label="luma", horizon_count=0),
        health=Health(allow_zero=True),
        previous_count=12,
    )

    assert verdict.blocked is False
    assert verdict.outcome is GateOutcome.EXEMPT
    assert verdict.overrides == ("allow_zero",)
    assert verdict.alerts == ()


def test_a_first_night_at_zero_is_not_a_decline():
    """No baseline means no comparison. A new source must not stop the presses."""
    verdict = evaluate_source(make_record(horizon_count=0), previous_count=None)

    assert verdict.blocked is False
    assert verdict.has("zero_no_baseline")


def test_zero_again_after_zero_does_not_reblock_every_night():
    verdict = evaluate_source(make_record(horizon_count=0), previous_count=0)

    assert verdict.blocked is False
    assert verdict.has("zero_again")


# --------------------------------------------------------------------------- global


def test_a_forty_five_percent_global_drop_blocks_publication(store: Store):
    night(store, [make_record(horizon_count=100)], total=100)

    verdict = evaluate_health(
        [make_record(horizon_count=55)],
        events=[event(f"e{i}") for i in range(55)],
        store=store,
        now=NOW,
    )

    assert verdict.blocks_publication is True
    assert any("dropped 45%" in reason for reason in verdict.reasons)
    assert verdict.previous_event_count == 100 and verdict.event_count == 55
    assert verdict.drop_fraction == pytest.approx(0.45)


def test_a_thirty_percent_global_drop_publishes(store: Store):
    """Under the 40% line. Calendars breathe; the gate is for collapses."""
    night(store, [make_record(horizon_count=100)], total=100)

    verdict = evaluate_health(
        [make_record(horizon_count=70)],
        events=[event(f"e{i}") for i in range(70)],
        store=store,
        now=NOW,
    )

    assert verdict.blocks_publication is False
    assert verdict.drop_fraction == pytest.approx(0.30)
    assert any("within the 40% threshold" in note for note in verdict.notes)


def test_exactly_forty_percent_is_not_more_than_forty_percent(store: Store):
    """"more than 40%" is strict. A threshold nobody can state exactly is a bug."""
    night(store, [make_record(horizon_count=100)], total=100)

    verdict = evaluate_health(
        [make_record(horizon_count=60)],
        events=[event(f"e{i}") for i in range(60)],
        store=store,
        now=NOW,
    )

    assert verdict.drop_fraction == pytest.approx(GLOBAL_DROP_THRESHOLD)
    assert verdict.blocks_publication is False


def test_the_global_gate_does_not_run_for_a_single_space_run(store: Store):
    """A --space run holds one space's events by construction."""
    night(store, [make_record(horizon_count=100)], total=100)

    verdict = evaluate_health(
        [make_record(horizon_count=3)],
        events=[event(f"e{i}") for i in range(3)],
        store=store,
        now=NOW,
        space_filter="hacker-dojo",
    )

    assert verdict.blocks_publication is False
    assert any("--space hacker-dojo" in note for note in verdict.notes)
    assert verdict.previous_event_count is None


def test_a_single_space_run_does_not_become_tomorrows_baseline(store: Store):
    run = store.start_run(NOW, space_filter="hacker-dojo", horizon_days=120)
    store.finish_run(run, event_count=3, published=False)
    night(store, [make_record(horizon_count=100)], total=100, when=NOW - dt.timedelta(days=1))

    previous, _ = previous_total(store, run_id=None)
    assert previous == 100, "the --space run must not be the comparison"


# --------------------------------------------------------------------------- capped feeds


def test_ignore_count_drop_exempts_hacker_dojos_capped_meetup_feed():
    """10-event cap, no pagination, ~1-week horizon. Nightly deltas are noise."""
    record = make_record(space_id="hacker-dojo", label="meetup-ical", horizon_count=2)

    without = evaluate_source(record, previous_count=10)
    assert without.has("count_drop")
    assert without.alerts, "an 80% drop is worth saying by default"

    with_override = evaluate_source(
        record, health=Health(ignore_count_drop=True), previous_count=10
    )
    assert with_override.blocked is False
    assert with_override.has("ignore_count_drop")
    assert with_override.alerts == ()
    assert with_override.overrides == ("ignore_count_drop",)


def test_ignore_count_drop_still_catches_the_capped_feed_going_to_zero():
    """The registry note is explicit: "only alert on 0 after a run with >0"."""
    verdict = evaluate_source(
        make_record(space_id="hacker-dojo", label="meetup-ical", horizon_count=0),
        health=Health(ignore_count_drop=True),
        previous_count=10,
    )

    assert verdict.blocked is True
    assert verdict.has("zero_after_nonzero")


def test_a_per_source_drop_never_blocks_because_a_short_horizon_is_not_a_decline():
    """Maker Nexus posts 4-8 weeks ahead: Aug 112, Sep 43, Oct 16.

    The far end of a 120-day window is legitimately empty every night, so a
    per-source delta is a thing to report and never a thing to stop for. Only a
    zero — which is unambiguous — blocks at source level.
    """
    verdict = evaluate_source(
        make_record(space_id="maker-nexus", label="gcal", horizon_count=60),
        previous_count=171,
    )

    assert verdict.blocked is False
    assert verdict.outcome is GateOutcome.DEGRADED
    assert verdict.has("count_drop")
    assert verdict.drop_fraction > SOURCE_DROP_THRESHOLD


def test_a_normal_nights_movement_is_not_reported_at_all():
    verdict = evaluate_source(
        make_record(space_id="maker-nexus", label="gcal", horizon_count=165),
        previous_count=171,
    )

    assert verdict.outcome is GateOutcome.OK
    assert verdict.reasons == ()


# --------------------------------------------------------------------------- require_nonzero_once


def test_require_nonzero_once_is_quiet_for_a_source_that_has_never_worked():
    """Humanmade's Eventbrite organizer: 0 upcoming, confirmed three ways.

    "Went to zero" can never fire for it, but a naive alert fires nightly.
    """
    verdict = evaluate_source(
        make_record(space_id="humanmade", label="eventbrite-organizer", horizon_count=0),
        health=Health(require_nonzero_once=True),
        previous_count=0,
        ever_nonzero=False,
    )

    assert verdict.blocked is False
    assert verdict.outcome is GateOutcome.EXEMPT
    assert verdict.overrides == ("require_nonzero_once",)
    assert verdict.alerts == ()


def test_require_nonzero_once_starts_gating_once_the_source_has_been_non_zero():
    """Hacker Dojo's Luma page may populate. The day it does, the gate applies."""
    verdict = evaluate_source(
        make_record(space_id="hacker-dojo", label="luma-calendar", horizon_count=0),
        health=Health(require_nonzero_once=True),
        previous_count=4,
        ever_nonzero=True,
    )

    assert verdict.blocked is True
    assert verdict.has("zero_after_nonzero")
    assert verdict.overrides == ()


def test_has_ever_returned_events_is_what_drives_it(store: Store):
    """The override reads persistent state, not tonight's run."""
    record = make_record(space_id="hacker-dojo", label="luma-calendar", horizon_count=0)
    quiet = evaluate_health(
        [record], registry=None, store=store, now=NOW
    ).source("hacker-dojo", "luma-calendar")
    assert quiet is not None and quiet.ever_nonzero is False

    night(store, [make_record(space_id="hacker-dojo", label="luma-calendar", horizon_count=4)])
    assert store.has_ever_returned_events("hacker-dojo", "luma-calendar") is True

    later = evaluate_health(
        [record], registry=None, store=store, now=NOW
    ).source("hacker-dojo", "luma-calendar")
    assert later is not None and later.ever_nonzero is True
    assert later.blocked is True


# ------------------------------------------------------------------- max_stale_days


def test_a_stale_feed_is_caught_even_though_every_count_is_perfect():
    """**The case the original count-based design missed.**

    Noisebridge's retired gCal: 28 VEVENTs, dead since January 2024, five RRULEs
    with no ``UNTIL``. It fabricates ~5 events a week *forever, at a constant
    rate*, so night over night the numbers are not merely fine — they are
    identical. Zero gate: passes. Per-source drop: zero drop. Global drop: no
    contribution. Every count-based check in the project says this feed is
    healthy, and it has been publishing invented events for two and a half years.

    ``max_stale_days`` on ``LAST-MODIFIED`` is the only thing that sees it.
    """
    abandoned = make_record(
        space_id="noisebridge",
        label="noisebridge-today",
        raw_count=28,
        horizon_count=71,
        stale_days=953.0,  # newest LAST-MODIFIED 2024-06-13
    )

    # Same record, same perfectly constant counts, no staleness gate configured.
    count_based_only = evaluate_source(abandoned, health=Health(), previous_count=71)
    assert count_based_only.blocked is False
    assert count_based_only.outcome is GateOutcome.OK
    assert count_based_only.reasons == (), "counting alone sees nothing wrong"

    # The gate the registry actually configures for it.
    gated = evaluate_source(
        abandoned, health=Health(max_stale_days=180), previous_count=71
    )
    assert gated.blocked is True
    assert gated.outcome is GateOutcome.BLOCKED
    assert gated.has("stale_feed")
    assert gated.horizon_count == 71, "it is still producing events; that is the point"
    assert gated.drop_fraction == 0.0, "and the count has not dropped at all"
    assert "953" in gated.blocking_reasons[0]
    assert gated.alerts


def test_the_registry_wires_max_stale_days_to_the_real_noisebridge_source(registry):
    """The gate reads ``sources.yaml``, not a value invented by the test."""
    index = health_index(registry)
    assert index["noisebridge:noisebridge-today"].max_stale_days == 180

    verdict = evaluate_health(
        [
            make_record(
                space_id="noisebridge",
                label="noisebridge-today",
                raw_count=28,
                horizon_count=71,
                stale_days=953.0,
            )
        ],
        registry=registry,
        now=NOW,
    )

    assert verdict.blocks_publication is True
    blocked = verdict.blocked_sources
    assert len(blocked) == 1 and blocked[0].has("stale_feed")


def test_a_feed_inside_its_staleness_budget_passes():
    verdict = evaluate_source(
        make_record(horizon_count=71, stale_days=12.0),
        health=Health(max_stale_days=180),
        previous_count=71,
    )
    assert verdict.blocked is False
    assert verdict.outcome is GateOutcome.OK


def test_a_feed_that_dates_nothing_is_reported_rather_than_assumed_fresh():
    """``stale_days`` of ``None`` is not the same as fresh."""
    verdict = evaluate_source(
        make_record(horizon_count=71, stale_days=None),
        health=Health(max_stale_days=180),
        previous_count=71,
    )
    assert verdict.blocked is False
    assert verdict.has("no_last_modified")
    assert verdict.alerts


# --------------------------------------------------------------------------- 304


def test_a_304_is_neither_a_zero_nor_stale():
    """Issue 0014's ``reused_unchanged``. Nothing changing is what a 304 *means*.

    ``apply_carry_forward`` builds the record, so this exercises the real seam
    between the two issues rather than a hand-set flag.
    """
    record = make_record(status="not_modified", horizon_count=0, event_count=0)
    record.stale_days = 400.0  # the stored feed has not changed in a long time

    class ReplayStore:
        def last_known_good_at(self, space_id, label):
            return NOW - dt.timedelta(hours=24)

        def carry_forward_events(self, space_id, label):
            return [event(f"e{i}") for i in range(9)]

    apply_carry_forward(record, ReplayStore(), now=NOW, horizon_days=120)
    assert record.reused_unchanged is True
    assert record.carried_forward is False

    verdict = evaluate_source(
        record, health=Health(max_stale_days=180), previous_count=9
    )

    assert verdict.blocked is False
    assert verdict.outcome is GateOutcome.OK
    assert verdict.has("reused_unchanged")
    assert verdict.horizon_count == 9, "a 304 contributed 9 events, not zero"
    assert not verdict.has("stale_feed"), "a 304 must not be penalized for staleness"


def test_a_304_run_does_not_trip_the_global_gate(store: Store):
    night(store, [make_record(horizon_count=9)], total=9)

    record = make_record(status="not_modified", horizon_count=9, reused_unchanged=True)
    verdict = evaluate_health(
        [record], events=[event(f"e{i}") for i in range(9)], store=store, now=NOW
    )

    assert verdict.blocks_publication is False


# --------------------------------------------------------------------------- carry-forward


def test_a_carried_forward_source_is_degraded_but_does_not_block():
    """Documented policy: carry-forward already owns this failure.

    Blocking here would take the other ten spaces off the calendar to punish one
    transient 503 — the exact inversion of the invariant that says a failed
    source must never silently delete a space.
    """
    record = make_record(status="failed", horizon_count=0, carried_forward=True)
    record.carry_forward_count = 7

    verdict = evaluate_source(record, previous_count=7)

    assert verdict.blocked is False
    assert verdict.outcome is GateOutcome.DEGRADED
    assert verdict.has("carried_forward")


def test_an_escalated_source_is_degraded_and_the_global_gate_sees_the_shortfall(
    store: Store,
):
    """Night three withholds the events, and that is what the global gate is for."""
    night(store, [make_record(horizon_count=100)], total=100)

    escalated = make_record(status="failed", horizon_count=0, escalated=True)
    verdict = evaluate_health(
        [escalated, make_record(space_id="ace", label="other", horizon_count=40)],
        events=[event(f"e{i}") for i in range(40)],
        store=store,
        now=NOW,
    )

    source = verdict.source("ace", "ics")
    assert source is not None
    assert source.outcome is GateOutcome.DEGRADED and source.blocked is False
    assert verdict.blocks_publication is True
    assert any("dropped 60%" in reason for reason in verdict.reasons)


def test_a_failure_night_is_not_the_baseline_for_the_next_night(store: Store):
    """A source that fails once then serves an empty document must still gate.

    Taking the literal previous run would record a zero the source never
    produced, and every night after that would compare zero against zero.
    """
    night(store, [make_record(horizon_count=12)], when=NOW - dt.timedelta(days=2))
    night(
        store,
        [make_record(status="failed", horizon_count=0)],
        when=NOW - dt.timedelta(days=1),
    )

    assert previous_source_count(store, "ace", "ics", run_id=None) == 12

    verdict = evaluate_health([make_record(horizon_count=0)], store=store, now=NOW)
    assert verdict.blocks_publication is True


# --------------------------------------------------------------------------- blocked


def test_a_robots_blocked_source_neither_blocks_publication_nor_alerts():
    """Documented policy. ``robots.txt`` said no and we honored it.

    It contributes zero events forever by design. Blocking would let one host's
    preference become our outage; alerting nightly for a state nobody intends to
    change is the noise that buries real alerts.
    """
    verdict = evaluate_source(
        make_record(space_id="lower-48", label="ics", status="blocked", horizon_count=0),
        previous_count=6,
    )

    assert verdict.blocked is False
    assert verdict.outcome is GateOutcome.EXEMPT
    assert verdict.has("blocked_by_robots")
    assert verdict.alerts == ()
    assert verdict.messages, "still visible in health.json, just not actionable"


def test_a_skipped_source_is_not_evaluated_at_all():
    verdict = evaluate_source(
        make_record(status="skipped", skipped_because="adapter_not_implemented")
    )
    assert verdict.outcome is GateOutcome.NOT_EVALUATED
    assert verdict.blocked is False


# --------------------------------------------------------------------------- counting rules


def test_the_gates_read_horizon_count_and_never_raw_count():
    """89 VEVENTs for ~7 live events; 3645 for 171; 5057 for 73.

    Both directions, because either one alone is satisfied by a coincidence.
    """
    # A feed still shipping 89 VEVENTs whose live events went to zero: raw says
    # healthy, horizon says the calendar is empty. The gate must see the zero.
    looks_full = evaluate_source(
        make_record(raw_count=89, horizon_count=0), previous_count=7
    )
    assert looks_full.blocked is True
    assert looks_full.has("zero_after_nonzero")

    # And a feed with no raw records left but a healthy expanded count is fine.
    looks_empty = evaluate_source(
        make_record(raw_count=0, horizon_count=7), previous_count=7
    )
    assert looks_empty.blocked is False
    assert looks_empty.horizon_count == 7


def test_the_night_over_night_comparison_is_also_horizon_count(store: Store):
    night(store, [make_record(raw_count=3645, horizon_count=171, event_count=171)])
    assert previous_source_count(store, "ace", "ics", run_id=None) == 171


def test_filters_removing_everything_is_said_out_loud_and_never_blocks():
    """Humanmade's ``title_contains: ["@ Humanmade"]`` doing its job looks like
    this from here, so it cannot be a blocking condition."""
    verdict = evaluate_source(
        make_record(space_id="humanmade", label="sf-hardware-meetup",
                    horizon_count=77, event_count=0),
        previous_count=77,
    )
    assert verdict.blocked is False
    assert verdict.has("filtered_to_zero")


def test_drop_fraction_has_no_opinion_without_a_baseline():
    assert drop_fraction(None, 5) is None
    assert drop_fraction(0, 5) is None
    assert drop_fraction(10, 12) == 0.0
    assert drop_fraction(10, 4) == pytest.approx(0.6)


# --------------------------------------------------------------------------- event audit


def test_the_audit_drops_events_outside_the_sanity_window():
    """Issue 0009 already does this at normalize. This is the last line."""
    audit = audit_events(
        [
            event("live"),
            event("yesterday", start=NOW - dt.timedelta(days=2)),
            event("2058", start=NOW + dt.timedelta(days=900)),
        ],
        now=NOW,
    )

    assert [e.uid for e in audit.events] == ["live"]
    assert audit.dropped_count == 2
    assert {reason.value for _, reason, _ in audit.dropped} == {"past", "too_far"}


def test_the_audit_quarantines_empty_and_runaway_titles():
    audit = audit_events(
        [event("ok"), event("blank", title=""), event("swallowed", title="x" * 201)],
        now=NOW,
    )

    assert [e.uid for e in audit.events] == ["ok"]
    assert audit.quarantined_count == 2
    assert {reason.value for _, reason in audit.quarantined} == {
        "empty_title",
        "title_too_long",
    }


def test_the_audit_does_not_double_count_what_normalize_already_dropped():
    """A healthy run finds nothing here, so the two layers never both count it."""
    record = make_record(horizon_count=1, dropped_count=4, quarantined_count=2)
    verdict = evaluate_health([record], events=[event("live")], now=NOW)

    assert verdict.dropped_count == 0
    assert verdict.quarantined_count == 0
    assert record.dropped_count == 4, "normalize's counts are untouched"
    assert verdict.event_count == 1


def test_the_audit_removes_bad_events_rather_than_blocking_the_run():
    """"drop and log" and "quarantine" are the stated remedies, not "block"."""
    verdict = evaluate_health(
        [make_record(horizon_count=2)],
        events=[event("live"), event("blank", title="")],
        now=NOW,
    )

    assert verdict.blocks_publication is False
    assert verdict.quarantined_count == 1
    assert [e.uid for e in verdict.events] == ["live"]


# --------------------------------------------------------------------------- config


def test_space_level_health_covers_every_source_it_owns(registry):
    """The Box Shop declares ``allow_zero`` once, at space level."""
    index = health_index(registry)
    assert index["the-box-shop:luma"].allow_zero is True
    assert index["the-box-shop:squarespace-events"].allow_zero is True


def test_merge_takes_the_tighter_staleness_budget():
    class Holder:
        def __init__(self, health):
            self.health = health

    space = Holder(Health(max_stale_days=365, allow_zero=True))
    source = Holder(Health(max_stale_days=180))
    merged = merge_health(space, source)

    assert merged.max_stale_days == 180, "the source's own statement wins"
    assert merged.allow_zero is True, "and the space's override still applies"

    inherited = merge_health(space, Holder(Health()))
    assert inherited.max_stale_days == 365


# --------------------------------------------------------------------------- the verdict


def test_the_verdict_is_json_ready_for_health_json_and_alerting(store: Store):
    """Issue 0017 writes this; issue 0032 alerts on it."""
    night(store, [make_record(horizon_count=12)], total=12)
    verdict = evaluate_health(
        [make_record(horizon_count=0)], events=[], store=store, now=NOW, run_id=None
    )

    payload = json.loads(json.dumps(verdict.as_dict()))
    for key in (
        "blocked",
        "reasons",
        "notes",
        "alerts",
        "gate_issue",
        "implemented",
        "event_count",
        "previous_event_count",
        "drop_fraction",
        "dropped_count",
        "quarantined_count",
        "thresholds",
        "global_reasons",
        "sources",
        "counts",
    ):
        assert key in payload

    assert payload["blocked"] is True
    assert payload["gate_issue"] == "0016"
    assert payload["counts"]["blocked"] == 1
    source = payload["sources"][0]
    assert source["key"] == "ace:ics"
    assert source["codes"] == ["zero_after_nonzero"]
    assert source["previous_count"] == 12 and source["horizon_count"] == 0
    assert "events" not in payload, "health.json reports counts, not the calendar"


def test_an_empty_run_with_no_history_is_not_blocked():
    verdict = evaluate_health([])
    assert isinstance(verdict, HealthVerdict)
    assert verdict.blocks_publication is False
    assert verdict.implemented is True


# --------------------------------------------------------------------------- the CLI


ICS_BODY = b"""BEGIN:VCALENDAR\r
VERSION:2.0\r
PRODID:-//test//maker-calendar//EN\r
X-WR-CALNAME:Test Feed\r
X-WR-TIMEZONE:America/Los_Angeles\r
BEGIN:VEVENT\r
UID:evt-1@test\r
DTSTAMP:20260804T120000Z\r
LAST-MODIFIED:20260804T120000Z\r
DTSTART:20260810T180000Z\r
DTEND:20260810T200000Z\r
SUMMARY:Open Shop Night\r
LOCATION:1234 Test Ave, Oakland, CA\r
DESCRIPTION:Come make something.\r
URL:https://example.test/events/1\r
END:VEVENT\r
END:VCALENDAR\r
"""

EMPTY_BODY = b"""BEGIN:VCALENDAR\r
VERSION:2.0\r
PRODID:-//test//maker-calendar//EN\r
X-WR-CALNAME:Test Feed\r
END:VCALENDAR\r
"""


def calendar_transport(body: bytes) -> httpx.MockTransport:
    """Every feed serves ``body``; ``robots.txt`` 404s. No ETags, so no 304s."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        payload = body
        if payload is ICS_BODY:
            slug = "".join(c if c.isalnum() else "-" for c in str(request.url))
            payload = payload.replace(b"UID:evt-1@test", f"UID:evt-{slug}@test".encode())
            payload = payload.replace(
                b"SUMMARY:Open Shop Night", f"SUMMARY:Workshop {slug[-12:]}".encode()
            )
        return httpx.Response(
            200, content=payload, headers={"Content-Type": "text/calendar; charset=utf-8"}
        )

    return httpx.MockTransport(handler)


def run_night(env, tmp_path: Path, body: bytes, when: dt.datetime) -> int:
    return main(
        ["run"],
        env=dict(env),
        env_file=tmp_path / "absent.env",
        out_dir=tmp_path / "out",
        raw_dir=tmp_path / "raw",
        db_path=tmp_path / "events.sqlite",
        transport=calendar_transport(body),
        sleep=lambda seconds: None,
        clock=lambda: 1000.0,
        now=when,
        llm_probe=lambda: __import__(
            "pipeline.cli", fromlist=["LmStudioStatus"]
        ).LmStudioStatus(available=False, reason="offline"),
    )


def test_the_cli_exits_one_and_writes_nothing_when_a_gate_blocks(env, tmp_path: Path):
    """The whole contract, end to end, over two nights against one store.

    Night one publishes. Night two every feed answers 200 with an empty
    calendar — the silent failure this project exists to catch — so the gates
    block, the exit code is 1 for launchd's log, and yesterday's calendar is
    left exactly as it was rather than being replaced with an empty one.
    """
    published = tmp_path / "out" / "calendar.ics"

    first = run_night(env, tmp_path, ICS_BODY, NOW)
    assert first == EXIT_OK
    assert published.exists()
    good = published.read_bytes()

    second = run_night(env, tmp_path, EMPTY_BODY, NOW + dt.timedelta(days=1))

    assert second == EXIT_HEALTH_BLOCKED
    assert published.read_bytes() == good, "the published calendar must be untouched"
    assert not (tmp_path / "out" / STAGING_DIRNAME).exists()

    with Store(tmp_path / "events.sqlite") as store:
        latest = store.latest_run()
        assert latest is not None
        assert latest.health_blocked is True
        assert latest.published is False
        assert latest.exit_code == EXIT_HEALTH_BLOCKED
        assert any("went to zero" in reason or "0 events" in reason
                   for reason in latest.health_reasons)
        rows = {row.source_key: row for row in store.sources_for_run(latest.run_id)}
        assert rows["hacker-dojo:meetup-ical"].gate_blocked is True


def test_two_healthy_nights_in_a_row_publish(env, tmp_path: Path):
    """The gates must not be a ratchet that blocks the second night of anything."""
    assert run_night(env, tmp_path, ICS_BODY, NOW) == EXIT_OK
    assert run_night(env, tmp_path, ICS_BODY, NOW + dt.timedelta(days=1)) == EXIT_OK
    assert (tmp_path / "out" / "calendar.ics").exists()
