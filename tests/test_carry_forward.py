"""Tests for carry-forward and for the store being wired into the CLI (issue 0014).

**No test here touches the network.** Every request goes through
``httpx.MockTransport``, including the ``robots.txt`` lookups the fetch layer
makes on its own. Issue 0001 is open, ``$MAKER_CALENDAR_CONTACT`` is unset on
this machine, and a live request would go out under a User-Agent pointing at a
page that does not exist.

What is being defended:

**A transient 503 must never silently delete a space from the calendar.** That
is a CLAUDE.md invariant, and the end-to-end pair at the bottom of this file is
the version of it that would actually catch a regression: a source that works on
night one and 503s on night two is still in night two's calendar.

**A ``blocked`` source must never carry forward.** ``robots.txt`` said no.
Republishing a stale copy is working around the file by another route, and it
would be the kind of violation nobody notices because it looks like everything
is fine. This is the single most important assertion in the file.

**Carried-forward data decays.** The horizon still applies, so last night's
event that has since happened drops out on its own rather than sitting on the
calendar forever.

**Three nights is not transient.** At the third consecutive failure the source
stops carrying forward and starts alerting, matching the repair trigger in
CLAUDE.md.

**Conditional GET is actually live.** Issue 0013 built the store and proved
``fetch.py`` worked against it; the CLI then constructed its ``Fetcher`` without
``state=``, so 19 MB of Sudo Room and Maker Nexus re-downloaded nightly for
nothing. The second run here must send ``If-None-Match`` — and must still
publish, because a 304 has no body to parse.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

import httpx
import pytest

from pipeline.carry_forward import (
    CARRY_FORWARD_STATUSES,
    ESCALATE_AFTER_NIGHTS,
    NEVER_CARRY_FORWARD,
    CarryReason,
    apply_carry_forward,
    clip_to_horizon,
    consecutive_failures,
    plan_carry_forward,
    within_horizon,
)
from pipeline.cli import EXIT_OK, STAGING_DIRNAME, SourceRecord, run_pipeline
from pipeline.config import CONTACT_ENV_VAR, load_registry
from pipeline.fetch import SourceState
from pipeline.normalize import Event
from pipeline.store import ReadOnlyStore, ReadOnlyStoreError, Store

TEST_CONTACT = "https://maker-calendar.test/about"

#: Night one, and the timestamp every stored event is recorded at.
NIGHT_ONE = dt.datetime(2026, 8, 5, 3, 15, 0, tzinfo=dt.timezone.utc)
#: Night two: the source is down.
NIGHT_TWO = NIGHT_ONE + dt.timedelta(days=1)

#: Noon Pacific on 4 August: inside the horizon on night one and in the past by
#: night two. The window floor is the start of *today* in the source's zone, and
#: at 03:15 UTC it is still the previous evening in California — which is
#: exactly why this constant is spelled out rather than derived from an offset.
LAST_NIGHTS_CLASS = dt.datetime(2026, 8, 4, 19, 0, 0, tzinfo=dt.timezone.utc)

SPACE = "sudo-room"
LABEL = "ics"
HORIZON = 120

ICS_BODY = b"""BEGIN:VCALENDAR\r
VERSION:2.0\r
PRODID:-//test//maker-calendar//EN\r
X-WR-CALNAME:Test Feed\r
X-WR-TIMEZONE:America/Los_Angeles\r
BEGIN:VEVENT\r
UID:evt-1@test\r
DTSTAMP:20260804T120000Z\r
LAST-MODIFIED:20260804T120000Z\r
DTSTART:20260910T180000Z\r
DTEND:20260910T200000Z\r
SUMMARY:Open Shop Night\r
LOCATION:1234 Test Ave, Oakland, CA\r
DESCRIPTION:Come make something.\r
URL:https://example.test/events/1\r
END:VEVENT\r
END:VCALENDAR\r
"""


# --------------------------------------------------------------------------- harness


def noop_sleep(seconds: float) -> None:
    """Rate limiting is 2 s per host and 10 s for Ace. Not in a test suite."""


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make_event(
    uid: str,
    start: dt.datetime,
    *,
    space_id: str = SPACE,
    label: str = LABEL,
    title: str = "Open Shop Night",
    tz: str = "America/Los_Angeles",
    **extra: object,
) -> Event:
    return Event(
        uid=uid,
        space_id=space_id,
        source_label=label,
        title=title,
        start_utc=start,
        end_utc=start + dt.timedelta(hours=2),
        tz=tz,
        content_hash=f"hash-{uid}",
        **extra,  # type: ignore[arg-type]
    )


@pytest.fixture
def store() -> Store:
    """A throwaway store holding one night's worth of Sudo Room events."""
    db = Store.in_memory()
    db.record_events(
        [
            make_event("sudo-room:a", NIGHT_ONE + dt.timedelta(days=3)),
            make_event("sudo-room:b", NIGHT_ONE + dt.timedelta(days=10)),
        ],
        now=NIGHT_ONE,
    )
    return db


def record_for(status: str, *, space_id: str = SPACE, label: str = LABEL) -> SourceRecord:
    record = SourceRecord(space_id=space_id, label=label, adapter="ics")
    record.status = status
    return record


def plan(store: Store, status: str, *, now: dt.datetime = NIGHT_TWO, horizon: int = HORIZON):
    return plan_carry_forward(
        store, SPACE, LABEL, status=status, now=now, horizon_days=horizon
    )


# --------------------------------------------------------------------------- failed


def test_a_failed_source_republishes_yesterdays_events(store: Store):
    """The invariant: a transient 503 does not delete a space from the calendar."""
    outcome = plan(store, "failed")

    assert outcome is not None
    assert outcome.reason is CarryReason.FAILED
    assert outcome.carried_forward is True
    assert [event.uid for event in outcome.events] == ["sudo-room:a", "sudo-room:b"]
    assert outcome.count == 2


def test_a_source_with_no_stored_history_carries_nothing(store: Store):
    """A source that has never worked has nothing to republish, and says so."""
    outcome = plan_carry_forward(
        store,
        "never-fetched",
        "ics",
        status="failed",
        now=NIGHT_TWO,
        horizon_days=HORIZON,
    )

    assert outcome is not None
    assert outcome.available is False
    assert outcome.events == ()
    assert "nothing to carry forward" in outcome.note


def test_an_unhandled_error_carries_forward_too(store: Store):
    """A crash in our parser is a bug in us, not a space cancelling everything."""
    outcome = plan(store, "error")

    assert outcome is not None
    assert outcome.carried_forward is True
    assert outcome.count == 2


# --------------------------------------------------------------------------- blocked


def test_a_blocked_source_never_carries_forward(store: Store):
    """**The important one.** robots.txt said no; a stale copy is a workaround.

    The store has two perfectly good events for this source. ``blocked`` must
    still produce nothing — CLAUDE.md: a disallow means we do not fetch it, not
    that we find an equivalent route.
    """
    assert store.carry_forward_events(SPACE, LABEL)  # the temptation is real
    assert plan(store, "blocked") is None


def test_a_blocked_source_leaves_the_record_untouched(store: Store):
    """No flag, no age, no failure count. Nothing to explain, because nothing ran."""
    record = record_for("blocked")
    assert apply_carry_forward(record, store, now=NIGHT_TWO, horizon_days=HORIZON) is None

    assert record.carried_forward is False
    assert record.carry_forward_count == 0
    assert record.carry_forward_age_seconds is None
    assert record.event_count == 0


def test_blocked_and_skipped_are_named_rather_than_merely_absent():
    """The refusal is spelled out in the module, not left to a missing branch."""
    assert "blocked" in NEVER_CARRY_FORWARD
    assert "skipped" in NEVER_CARRY_FORWARD
    assert not (NEVER_CARRY_FORWARD & CARRY_FORWARD_STATUSES)


# --------------------------------------------------------------------------- skipped


def test_a_skipped_source_never_carries_forward(store: Store):
    """``enabled: false`` is a decision. Reviving its events would undo it."""
    assert plan(store, "skipped") is None


def test_a_healthy_source_has_no_carry_forward_plan(store: Store):
    assert plan(store, "ok") is None


# --------------------------------------------------------------------------- horizon


def test_carried_forward_events_that_left_the_horizon_are_dropped():
    """Yesterday's events that have since happened still drop out."""
    db = Store.in_memory()
    db.record_events(
        [
            # Live when it was stored, over by the time it would be republished.
            make_event("sudo-room:gone", LAST_NIGHTS_CLASS),
            make_event("sudo-room:live", NIGHT_ONE + dt.timedelta(days=5)),
        ],
        now=NIGHT_ONE,
    )

    assert within_horizon(
        make_event("sudo-room:gone", LAST_NIGHTS_CLASS), now=NIGHT_ONE, horizon_days=HORIZON
    ), "the fixture must have been publishable on night one"

    outcome = plan(db, "failed")

    assert outcome is not None
    assert [event.uid for event in outcome.events] == ["sudo-room:live"]
    assert outcome.dropped_uids == ("sudo-room:gone",)
    assert outcome.dropped_count == 1
    assert "left the 120-day horizon" in outcome.note


def test_carry_forward_does_not_republish_beyond_the_horizon():
    """A 200-day-out event is out of a 120-day window, carried forward or not."""
    db = Store.in_memory()
    db.record_events(
        [make_event("sudo-room:far", NIGHT_ONE + dt.timedelta(days=200))],
        now=NIGHT_ONE,
    )

    outcome = plan(db, "failed")

    assert outcome is not None
    assert outcome.events == ()
    assert outcome.dropped_uids == ("sudo-room:far",)


def test_within_horizon_keeps_an_all_day_event_through_its_last_day():
    """An all-day event is current for the whole of its last day, as in normalize."""
    festival = make_event(
        "sudo-room:festival",
        NIGHT_ONE - dt.timedelta(days=1),
        all_day=True,
        start_date=(NIGHT_ONE - dt.timedelta(days=1)).date(),
        end_date=NIGHT_TWO.date(),
    )
    assert within_horizon(festival, now=NIGHT_TWO, horizon_days=HORIZON) is True

    over = make_event(
        "sudo-room:over",
        NIGHT_ONE - dt.timedelta(days=3),
        all_day=True,
        start_date=(NIGHT_ONE - dt.timedelta(days=3)).date(),
        end_date=(NIGHT_ONE - dt.timedelta(days=2)).date(),
    )
    assert within_horizon(over, now=NIGHT_TWO, horizon_days=HORIZON) is False


def test_clip_to_horizon_reports_both_halves():
    kept, dropped = clip_to_horizon(
        [
            make_event("keep", NIGHT_ONE + dt.timedelta(days=4)),
            make_event("drop", NIGHT_ONE - dt.timedelta(days=4)),
        ],
        now=NIGHT_TWO,
        horizon_days=HORIZON,
    )
    assert [event.uid for event in kept] == ["keep"]
    assert dropped == ["drop"]


# --------------------------------------------------------------------------- the record


def test_the_record_carries_the_flag_and_the_age_of_the_data(store: Store):
    """Issue 0017 builds ``health.json`` staleness out of exactly these fields."""
    record = record_for("failed")
    outcome = apply_carry_forward(record, store, now=NIGHT_TWO, horizon_days=HORIZON)

    assert outcome is not None
    assert record.carried_forward is True
    assert record.carry_forward_count == 2
    assert record.carry_forward_age_seconds == pytest.approx(86400.0)
    assert record.carry_forward_age_days == pytest.approx(1.0)
    assert record.last_known_good_at == NIGHT_ONE.isoformat()
    assert record.consecutive_failures == 1
    assert record.escalated is False

    data = record.as_dict()
    assert data["carried_forward"] is True
    assert data["carry_forward_count"] == 2
    assert data["carry_forward_age_days"] == pytest.approx(1.0)
    assert data["last_known_good_at"] == NIGHT_ONE.isoformat()


def test_a_carried_forward_source_still_reports_zero_events_of_its_own(store: Store):
    """The gates (issue 0016) must see the failure, not the bridge over it."""
    record = record_for("failed")
    apply_carry_forward(record, store, now=NIGHT_TWO, horizon_days=HORIZON)

    assert record.event_count == 0
    assert record.carry_forward_count == 2
    assert "carried forward 2 events, 1.0d old" in record.line()


# --------------------------------------------------------------------------- escalation


def test_three_consecutive_failures_escalates_instead_of_carrying_forward(store: Store):
    """Carry-forward is a bridge, not a preservative. Three nights is not transient."""
    store.put(
        f"{SPACE}:{LABEL}",
        SourceState(last_status=503, consecutive_failures=ESCALATE_AFTER_NIGHTS),
    )

    outcome = plan(store, "failed", now=NIGHT_ONE + dt.timedelta(days=3))

    assert outcome is not None
    assert outcome.escalated is True
    assert outcome.events == ()
    assert outcome.consecutive_failures == 3
    assert outcome.alert is not None
    assert "failed 3 nights running" in outcome.alert
    assert "2 events withheld" in outcome.alert


def test_two_consecutive_failures_still_carries_forward(store: Store):
    """Night two is still transient. The space stays on the calendar."""
    store.put(f"{SPACE}:{LABEL}", SourceState(last_status=503, consecutive_failures=2))

    outcome = plan(store, "failed")

    assert outcome is not None
    assert outcome.escalated is False
    assert outcome.count == 2
    assert outcome.consecutive_failures == 2


def test_escalation_counts_parse_failures_the_fetch_layer_resets(store: Store):
    """A feed answering 200 with the homepage every night must still escalate.

    ``SourceState.consecutive_failures`` is reset by a successful *transport*,
    so the wrong-adapter case — "as silent as a wrong URL" — would sit at zero
    forever on that counter alone. The run history is what catches it.
    """
    for _ in range(2):
        run_id = store.start_run(NIGHT_ONE, horizon_days=HORIZON)
        record = record_for("failed")
        store.record_source_run(run_id, record)
        store.finish_run(run_id)

    # The transport succeeded every night, so the fetch counter says zero.
    assert (store.get(f"{SPACE}:{LABEL}") or SourceState()).consecutive_failures == 0
    assert store.consecutive_failed_runs(SPACE, LABEL) == 2
    assert consecutive_failures(store, SPACE, LABEL) == 3

    outcome = plan(store, "failed")
    assert outcome is not None
    assert outcome.escalated is True


def test_a_successful_night_breaks_the_failure_streak(store: Store):
    for status in ("failed", "failed", "ok"):
        run_id = store.start_run(NIGHT_ONE, horizon_days=HORIZON)
        store.record_source_run(run_id, record_for(status))
        store.finish_run(run_id)

    assert store.consecutive_failed_runs(SPACE, LABEL) == 0


def test_a_dry_run_cannot_push_a_source_toward_its_third_strike(store: Store):
    """A debugging invocation must never be able to fire an alert."""
    for _ in range(5):
        run_id = store.start_run(NIGHT_ONE, dry_run=True, horizon_days=HORIZON)
        store.record_source_run(run_id, record_for("failed"))
        store.finish_run(run_id)

    assert store.consecutive_failed_runs(SPACE, LABEL) == 0


def test_the_escalated_record_carries_the_alert(store: Store):
    store.put(f"{SPACE}:{LABEL}", SourceState(consecutive_failures=4))
    record = record_for("failed")
    apply_carry_forward(record, store, now=NIGHT_TWO, horizon_days=HORIZON)

    assert record.escalated is True
    assert record.carry_forward_count == 0
    assert record.alert and "repair workflow" in record.alert
    assert "ESCALATED" in record.line()


# --------------------------------------------------------------------------- 304


def test_a_304_is_not_a_failure_and_does_not_carry_forward(store: Store):
    """The server said the bytes are current. Nothing is stale and nothing escalates."""
    outcome = plan(store, "not_modified")

    assert outcome is not None
    assert outcome.reason is CarryReason.NOT_MODIFIED
    assert outcome.carried_forward is False
    assert outcome.reused_unchanged is True
    assert outcome.escalated is False
    assert outcome.consecutive_failures == 0
    assert outcome.count == 2


def test_a_304_record_reads_as_a_healthy_source(store: Store):
    """A 'went to zero' gate must not fire on the one outcome that means unchanged."""
    record = record_for("not_modified")
    apply_carry_forward(record, store, now=NIGHT_TWO, horizon_days=HORIZON)

    assert record.carried_forward is False
    assert record.reused_unchanged is True
    assert record.event_count == 2
    assert record.horizon_count == 2
    assert record.escalated is False


def test_a_304_still_respects_the_horizon():
    """Unchanged bytes do not make a past event current again."""
    db = Store.in_memory()
    db.record_events([make_event("sudo-room:gone", LAST_NIGHTS_CLASS)], now=NIGHT_ONE)

    outcome = plan(db, "not_modified")

    assert outcome is not None
    assert outcome.events == ()
    assert outcome.dropped_uids == ("sudo-room:gone",)


# --------------------------------------------------------------------------- read-only


def test_the_read_only_store_reads_state_and_drops_writes(store: Store):
    facade = ReadOnlyStore(store)
    key = f"{SPACE}:{LABEL}"
    store.put(key, SourceState(etag='"v1"'))

    assert facade.get(key) is not None
    assert (facade.get(key) or SourceState()).etag == '"v1"'

    facade.put(key, SourceState(etag='"v2"'))
    assert (store.get(key) or SourceState()).etag == '"v1"'

    assert facade.carry_forward_events(SPACE, LABEL)  # reads pass through


def test_the_read_only_store_refuses_the_writes_that_would_leave_a_mark(store: Store):
    facade = ReadOnlyStore(store)
    with pytest.raises(ReadOnlyStoreError):
        facade.record_events([])
    with pytest.raises(ReadOnlyStoreError):
        facade.start_run()


# --------------------------------------------------------------------------- end to end

# Everything below drives the real CLI against httpx.MockTransport.


@pytest.fixture
def env() -> dict[str, str]:
    return {CONTACT_ENV_VAR: TEST_CONTACT}


@pytest.fixture
def registry(env: dict[str, str]):
    return load_registry(env=env)


def offline_llm():
    from pipeline.cli import LmStudioStatus

    return LmStudioStatus(available=False, reason="ConnectError: not running")


def feed_body(url: httpx.URL) -> bytes:
    """One event per feed, with a UID **and a title** derived from the feed.

    The title varies as well as the UID since issue 0015: dedupe merges
    same-space events that start within 30 minutes and carry near-identical
    titles, so a fixture serving one identically-named event to both of a
    space's feeds would legitimately collapse to a single record and the
    carry-forward counts here would be measuring dedupe instead.
    """
    slug = "".join(char if char.isalnum() else "-" for char in str(url))
    digest = hashlib.sha1(str(url).encode()).hexdigest()[:8]
    return ICS_BODY.replace(
        b"UID:evt-1@test", f"UID:evt-{slug}@test".encode()
    ).replace(b"SUMMARY:Open Shop Night", f"SUMMARY:Workshop {digest}".encode())


def healthy_transport(etag: str | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        headers = {"Content-Type": "text/calendar; charset=utf-8"}
        if etag:
            headers["ETag"] = etag
        return httpx.Response(200, content=feed_body(request.url), headers=headers)

    return httpx.MockTransport(handler)


def down_transport(status: int = 503) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(status, content=b"upstream is having a day")

    return httpx.MockTransport(handler)


def night(registry, tmp_path: Path, db: Path, *, when, transport, **kwargs):
    return run_pipeline(
        registry,
        space_id="hacker-dojo",
        out_dir=tmp_path / "out",
        raw_dir=tmp_path / "raw",
        db_path=db,
        transport=transport,
        sleep=noop_sleep,
        clock=FakeClock(),
        now=when,
        llm_probe=offline_llm,
        **kwargs,
    )


def test_a_source_that_503s_on_night_two_is_still_in_night_twos_calendar(
    registry, tmp_path: Path
):
    """The whole issue, end to end. A transient 503 deletes nothing."""
    db = tmp_path / "events.sqlite"

    first = night(registry, tmp_path, db, when=NIGHT_ONE, transport=healthy_transport())
    assert first.event_count == 2
    assert first.published is True
    assert [record.status for record in first.records] == ["ok", "ok"]

    second = night(registry, tmp_path, db, when=NIGHT_TWO, transport=down_transport())

    assert [record.status for record in second.records] == ["failed", "failed"]
    assert second.event_count == 2, "the space fell out of the calendar"
    assert {event.uid for event in second.events} == {
        event.uid for event in first.events
    }
    assert second.published is True
    assert len(second.carried_forward) == 2
    assert second.carried_forward_event_count == 2
    assert all(
        record.carry_forward_age_seconds == pytest.approx(86400.0)
        for record in second.carried_forward
    )

    calendar = (tmp_path / "out" / STAGING_DIRNAME / "calendar.ics").read_bytes()
    assert calendar.count(b"BEGIN:VEVENT") == 2
    assert second.exit_code == EXIT_OK


def test_a_blocked_night_two_drops_the_space_from_the_calendar(registry, tmp_path: Path):
    """The counterpart, and the one that matters: robots.txt is not routed around."""
    db = tmp_path / "events.sqlite"
    night(registry, tmp_path, db, when=NIGHT_ONE, transport=healthy_transport())

    def blocked(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200,
                content=b"User-agent: *\nDisallow: /\n",
                headers={"Content-Type": "text/plain"},
            )
        raise AssertionError(f"must not fetch a disallowed path: {request.url}")

    second = night(
        registry, tmp_path, db, when=NIGHT_TWO, transport=httpx.MockTransport(blocked)
    )

    assert [record.status for record in second.records] == ["blocked", "blocked"]
    assert second.event_count == 0
    assert second.carried_forward == []
    assert second.carry_forwards == []
    assert second.published is False


def test_conditional_get_is_live_the_second_run_sends_if_none_match(
    registry, tmp_path: Path
):
    """Issue 0013's whole point: 19 MB of Sudo Room and Maker Nexus, or a 304."""
    db = tmp_path / "events.sqlite"
    night(
        registry, tmp_path, db, when=NIGHT_ONE, transport=healthy_transport(etag='"v1"')
    )

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        seen.append(request.headers.get("If-None-Match", ""))
        return httpx.Response(304, headers={"ETag": '"v1"'})

    second = night(
        registry, tmp_path, db, when=NIGHT_TWO, transport=httpx.MockTransport(handler)
    )

    assert seen == ['"v1"', '"v1"'], "the CLI is not passing the store to Fetcher"
    assert [record.status for record in second.records] == [
        "not_modified",
        "not_modified",
    ]
    assert all(record.conditional for record in second.records)

    # A 304 has no body to parse, so without the replay the space would vanish
    # the moment conditional GET was switched on.
    assert second.event_count == 2
    assert second.published is True
    assert all(record.reused_unchanged for record in second.records)
    assert not second.carried_forward
    assert second.alerts == []


def test_the_stored_etag_survives_the_process(registry, tmp_path: Path):
    """The state is in SQLite, not in a dict that dies with the run."""
    db = tmp_path / "events.sqlite"
    night(
        registry, tmp_path, db, when=NIGHT_ONE, transport=healthy_transport(etag='"v7"')
    )

    with Store(db) as reopened:
        states = reopened.source_states()
    assert [state.etag for state in states.values()] == ['"v7"', '"v7"']


def test_the_run_is_recorded_with_its_sources(registry, tmp_path: Path):
    db = tmp_path / "events.sqlite"
    report = night(
        registry, tmp_path, db, when=NIGHT_ONE, transport=healthy_transport()
    )

    assert report.run_id is not None
    with Store(db) as reopened:
        row = reopened.get_run(report.run_id)
        sources = reopened.sources_for_run(report.run_id)

    assert row is not None
    assert row.event_count == 2
    assert row.published is True
    assert row.finished_at is not None
    assert {source.label for source in sources} == {"luma-calendar", "meetup-ical"}
    assert all(source.status == "ok" for source in sources)


def test_record_events_is_called_once_per_source_per_run(registry, tmp_path: Path):
    """``carry_forward_events`` keys on ``MAX(last_seen)``.

    Two calls for one source in one run would split that night's set across two
    timestamps, and tomorrow's carry-forward would republish only the tail.
    """
    calls: list[tuple[str, ...]] = []

    class CountingStore(Store):
        def record_events(self, events, **kwargs):  # type: ignore[override]
            events = list(events)
            calls.append(tuple(sorted({event.source_label for event in events})))
            return super().record_events(events, **kwargs)

    with CountingStore(tmp_path / "events.sqlite") as db:
        run_pipeline(
            registry,
            space_id="hacker-dojo",
            out_dir=tmp_path / "out",
            raw_dir=tmp_path / "raw",
            store=db,
            transport=healthy_transport(),
            sleep=noop_sleep,
            clock=FakeClock(),
            now=NIGHT_ONE,
            llm_probe=offline_llm,
        )

        assert len(calls) == 2
        assert sorted(calls) == [("luma-calendar",), ("meetup-ical",)]
        for label in ("luma-calendar", "meetup-ical"):
            assert len(db.carry_forward_events("hacker-dojo", label)) == 1


def test_a_dry_run_leaves_the_store_exactly_as_it_found_it(registry, tmp_path: Path):
    """The policy, asserted: --dry-run reads everything and writes nothing.

    A stored ETag would make the *next real run* receive a 304 for a body this
    process parsed and threw away, so the published calendar would come out of
    replayed history because a debugging invocation spent the only
    unconditional fetch.
    """
    db = tmp_path / "events.sqlite"

    report = night(
        registry,
        tmp_path,
        db,
        when=NIGHT_ONE,
        transport=healthy_transport(etag='"v1"'),
        dry_run=True,
    )

    assert report.event_count == 2
    assert report.run_id is None
    with Store(db) as reopened:
        assert reopened.source_states() == {}
        assert reopened.event_count() == 0
        assert reopened.runs() == []


def test_a_dry_run_reads_stored_state_so_it_is_realistic(registry, tmp_path: Path):
    """Reads, not writes. The dry run still sends If-None-Match."""
    db = tmp_path / "events.sqlite"
    night(
        registry, tmp_path, db, when=NIGHT_ONE, transport=healthy_transport(etag='"v1"')
    )

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        seen.append(request.headers.get("If-None-Match", ""))
        return httpx.Response(304, headers={"ETag": '"v2"'})

    night(
        registry,
        tmp_path,
        db,
        when=NIGHT_TWO,
        transport=httpx.MockTransport(handler),
        dry_run=True,
    )

    assert seen == ['"v1"', '"v1"']
    with Store(db) as reopened:
        # The server offered a new validator; the dry run did not take it.
        assert [state.etag for state in reopened.source_states().values()] == [
            '"v1"',
            '"v1"',
        ]


def test_a_run_with_no_database_named_writes_nothing_to_disk(registry, tmp_path: Path):
    """The library default is in-memory: a forgetful run, never the real db/."""
    report = run_pipeline(
        registry,
        space_id="hacker-dojo",
        out_dir=tmp_path / "out",
        raw_dir=tmp_path / "raw",
        transport=healthy_transport(),
        sleep=noop_sleep,
        clock=FakeClock(),
        now=NIGHT_ONE,
        llm_probe=offline_llm,
    )

    assert report.db_path is None
    assert not (tmp_path / "events.sqlite").exists()
