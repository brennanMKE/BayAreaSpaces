"""Tests for the SQLite working store (issue 0013).

Three things are being defended, and one of them is the reason the issue exists.

**``first_seen`` survives a content change.** A space editing a title is not a
new event. ``content_hash`` moves; ``first_seen`` does not. Issue 0018 reads
that column as RSS ``pubDate``, so a store that re-dates an edited event
re-notifies every subscriber about an event they already have. That is the
regression, and it is asserted from both directions: content changed, and
content unchanged across two runs.

**Conditional GET actually works across process restarts.** ``fetch.py`` was
written against the :class:`~pipeline.fetch.HttpStateStore` protocol in issue
0006 and must not need a line changed to use this. The 304 path is exercised
end to end through ``httpx.MockTransport``, including the case that matters —
a *second* ``Fetcher`` against a *reopened* database, which is what a nightly
launchd job actually is. Two feeds in this registry are 8 MB and 11 MB.

**``require_nonzero_once`` is answerable.** "Has this source ever returned
events?" is persistent state, and it must stay true through a zero night;
otherwise the gate degrades into "did it return zero tonight", which is the
question CLAUDE.md says is wrong for Humanmade's Eventbrite and Hacker Dojo's
Luma.

No test here touches the network.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

import httpx
import pytest

from pipeline.config import Registry
from pipeline.fetch import Fetcher, HttpStateStore, Outcome, SourceState
from pipeline.normalize import AddressSource, Event, QuarantineReason
from pipeline.store import (
    SCHEMA_VERSION,
    NaiveDatetimeError,
    SchemaTooNewError,
    Store,
    event_from_dict,
    from_iso,
    open_store,
    to_iso,
)

TEST_CONTACT = "https://maker-calendar.test/about"
USER_AGENT = f"bayarea-maker-calendar/0.1 (+{TEST_CONTACT})"

RUN_1 = dt.datetime(2026, 8, 5, 3, 15, 0, tzinfo=dt.timezone.utc)
RUN_2 = dt.datetime(2026, 8, 6, 3, 15, 0, tzinfo=dt.timezone.utc)
RUN_3 = dt.datetime(2026, 8, 7, 3, 15, 0, tzinfo=dt.timezone.utc)

ICS_BODY = b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n"


# --------------------------------------------------------------------------- harness


def make_event(
    uid: str = "ace:evt-1",
    *,
    space_id: str = "ace",
    label: str = "ics",
    title: str = "Open Shop Night",
    start: dt.datetime | None = None,
    content_hash: str = "hash-1",
    first_seen: dt.datetime | None = None,
    **kwargs,
) -> Event:
    return Event(
        uid=uid,
        space_id=space_id,
        source_label=label,
        title=title,
        start_utc=start or dt.datetime(2026, 8, 12, 2, 0, tzinfo=dt.timezone.utc),
        end_utc=dt.datetime(2026, 8, 12, 5, 0, tzinfo=dt.timezone.utc),
        tz="America/Los_Angeles",
        content_hash=content_hash,
        first_seen=first_seen,
        **kwargs,
    )


class Router:
    """A ``httpx.MockTransport`` handler with a per-URL response queue."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self._routes: dict[str, list] = {}

    def add(self, url: str, *responses) -> Router:
        self._routes.setdefault(url, []).extend(responses)
        return self

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)  # no robots.txt: everything allowed
        queue = self._routes.get(str(request.url))
        if not queue:
            raise AssertionError(f"unexpected request to {request.url}")
        item = queue[0] if len(queue) == 1 else queue.pop(0)
        return item(request)

    @property
    def target_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path != "/robots.txt"]


def reply(status: int = 200, body: bytes = b"", content_type: str | None = None, **headers):
    def build(request: httpx.Request) -> httpx.Response:
        all_headers = dict(headers)
        if content_type is not None:
            all_headers["Content-Type"] = content_type
        return httpx.Response(status, content=body, headers=all_headers)

    return build


FEED_URL = "https://ace.example.org/events.ics"


def make_registry() -> Registry:
    return Registry.model_validate(
        {
            "defaults": {
                "timezone": "America/Los_Angeles",
                "user_agent": USER_AGENT,
                "rate_limit_seconds": 0.01,
                "horizon_days": 120,
            },
            "spaces": [
                {
                    "id": "ace",
                    "name": "Ace Makerspace",
                    "city": "Oakland",
                    "region": "East Bay",
                    "url": "https://ace.example.org",
                    "sources": [{"adapter": "ics", "url": FEED_URL}],
                }
            ],
        }
    )


def make_fetcher(registry: Registry, router: Router, tmp_path: Path, store: Store) -> Fetcher:
    return Fetcher(
        registry,
        transport=httpx.MockTransport(router),
        raw_dir=tmp_path / "raw",
        state=store,
        sleep=lambda seconds: None,
        clock=lambda: 0.0,
        now=lambda: RUN_1,
    )


class FakeSourceRecord:
    """The shape :class:`pipeline.cli.SourceRecord` presents to the store.

    Used verbatim rather than importing the CLI, which is the point: the store
    sits underneath ``cli`` and takes the record structurally so the two cannot
    form an import cycle.
    """

    def __init__(self, space_id="ace", label="ics", **kwargs):
        self.space_id = space_id
        self.label = label
        self.adapter = kwargs.get("adapter", "ics")
        self.status = kwargs.get("status", "ok")
        self.http_status = kwargs.get("http_status", 200)
        self.content_type = kwargs.get("content_type", "text/calendar")
        self.bytes = kwargs.get("bytes", 8_400_000)
        self.conditional = kwargs.get("conditional", False)
        self.attempts = kwargs.get("attempts", 1)
        self.raw_count = kwargs.get("raw_count", 89)
        self.horizon_count = kwargs.get("horizon_count", 7)
        self.normalized_count = kwargs.get("normalized_count", 7)
        self.event_count = kwargs.get("event_count", 7)
        self.dropped_count = kwargs.get("dropped_count", 0)
        self.quarantined_count = kwargs.get("quarantined_count", 0)
        self.filtered_out_count = kwargs.get("filtered_out_count", 0)
        self.fetch_seconds = kwargs.get("fetch_seconds", 1.5)
        self.elapsed_seconds = kwargs.get("elapsed_seconds", 2.25)
        self.problem = kwargs.get("problem")
        self.reason = kwargs.get("reason")
        self.error = kwargs.get("error")


# --------------------------------------------------------------------------- schema


def test_a_fresh_database_initializes_and_reports_its_schema_version(tmp_path):
    store = Store(tmp_path / "db" / "events.sqlite")
    assert store.schema_version == SCHEMA_VERSION
    assert store.migrated_from == 0
    assert store.applied_migrations() == list(range(1, SCHEMA_VERSION + 1))
    store.close()


def test_the_store_creates_the_db_directory_on_demand(tmp_path):
    # db/ is gitignored and does not exist on a fresh checkout.
    db_path = tmp_path / "db" / "events.sqlite"
    assert not db_path.parent.exists()
    with Store(db_path) as store:
        assert store.schema_version == SCHEMA_VERSION
    assert db_path.exists()


def test_open_store_accepts_an_explicit_path(tmp_path):
    with open_store(tmp_path / "db" / "events.sqlite") as store:
        assert store.schema_version == SCHEMA_VERSION


def test_migrations_run_idempotently_when_the_database_is_reopened(tmp_path):
    db_path = tmp_path / "db" / "events.sqlite"
    first = Store(db_path)
    first.put("ace:ics", SourceState(etag='"v1"'))
    first.close()

    second = Store(db_path)
    assert second.migrated_from == SCHEMA_VERSION  # nothing left to apply
    assert second.schema_version == SCHEMA_VERSION
    assert second.applied_migrations() == list(range(1, SCHEMA_VERSION + 1))
    # Reopening must not have dropped or recreated anything.
    state = second.get("ace:ics")
    assert state is not None and state.etag == '"v1"'
    second.close()


def test_migrations_are_idempotent_over_many_opens(tmp_path):
    db_path = tmp_path / "db" / "events.sqlite"
    for _ in range(5):
        Store(db_path).close()
    with Store(db_path) as store:
        assert store.applied_migrations() == list(range(1, SCHEMA_VERSION + 1))


def test_a_newer_schema_is_refused_rather_than_half_read(tmp_path):
    # A schema surprise at 03:15 is expensive; failing loudly beats guessing.
    db_path = tmp_path / "db" / "events.sqlite"
    Store(db_path).close()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
        (SCHEMA_VERSION + 7, RUN_1.isoformat()),
    )
    conn.commit()
    conn.close()

    with pytest.raises(SchemaTooNewError) as excinfo:
        Store(db_path)
    assert str(SCHEMA_VERSION + 7) in str(excinfo.value)


# --------------------------------------------------------------------------- HTTP state


def test_the_store_satisfies_the_http_state_store_protocol():
    with Store.in_memory() as store:
        assert isinstance(store, HttpStateStore)


def test_an_unknown_source_key_has_no_state():
    with Store.in_memory() as store:
        assert store.get("ace:ics") is None


def test_etag_state_round_trips_through_sqlite(tmp_path):
    state = SourceState(
        etag='"686897696a7c876b7e"',
        last_modified="Wed, 05 Aug 2026 03:15:00 GMT",
        last_status=200,
        last_success_at=RUN_1,
        last_seen=RUN_1,
        consecutive_failures=0,
        last_content_hash="a" * 64,
    )
    db_path = tmp_path / "db" / "events.sqlite"
    with Store(db_path) as store:
        store.put("ace:ics", state)

    with Store(db_path) as reopened:  # a new process, which is what launchd is
        back = reopened.get("ace:ics")
    assert back == state


def test_put_replaces_rather_than_accumulating():
    with Store.in_memory() as store:
        store.put("ace:ics", SourceState(etag='"v1"', consecutive_failures=3))
        store.put("ace:ics", SourceState(etag='"v2"', consecutive_failures=0))
        state = store.get("ace:ics")
        assert state is not None
        assert state.etag == '"v2"'
        assert state.consecutive_failures == 0
        assert len(store.source_states()) == 1


def test_source_states_returns_every_key():
    with Store.in_memory() as store:
        store.put("ace:ics", SourceState(etag='"a"'))
        store.put("sudo-room:gcal_ics", SourceState(etag='"b"'))
        assert set(store.source_states()) == {"ace:ics", "sudo-room:gcal_ics"}


def test_forget_source_state_makes_the_next_get_unconditional():
    with Store.in_memory() as store:
        store.put("ace:ics", SourceState(etag='"stuck"'))
        store.forget_source_state("ace:ics")
        assert store.get("ace:ics") is None


def test_storing_a_naive_datetime_raises():
    with Store.in_memory() as store:
        with pytest.raises(NaiveDatetimeError):
            store.put("ace:ics", SourceState(last_seen=dt.datetime(2026, 8, 5, 3, 15)))
        with pytest.raises(NaiveDatetimeError):
            store.put(
                "ace:ics", SourceState(last_success_at=dt.datetime(2026, 8, 5, 3, 15))
            )


def test_storing_a_naive_run_timestamp_raises():
    with Store.in_memory() as store:
        with pytest.raises(NaiveDatetimeError):
            store.start_run(dt.datetime(2026, 8, 5, 3, 15))
        with pytest.raises(NaiveDatetimeError):
            store.record_events([make_event()], now=dt.datetime(2026, 8, 5, 3, 15))


def test_to_iso_and_from_iso_round_trip_in_utc():
    local = dt.datetime(2026, 8, 4, 20, 15, tzinfo=dt.timezone(dt.timedelta(hours=-7)))
    text = to_iso(local)
    assert text == "2026-08-05T03:15:00+00:00"
    assert from_iso(text) == RUN_1


def test_from_iso_refuses_a_row_stored_without_a_timezone():
    with pytest.raises(NaiveDatetimeError):
        from_iso("2026-08-05T03:15:00")


# ------------------------------------------------------- fetch.py against the real store


def test_fetch_stores_the_etag_it_was_given(tmp_path):
    router = Router().add(FEED_URL, reply(200, ICS_BODY, "text/calendar", ETag='"v1"'))
    with Store.in_memory() as store:
        fetcher = make_fetcher(make_registry(), router, tmp_path, store)
        result = next(fetcher.fetch_all())
        assert result.outcome is Outcome.FETCHED
        state = store.get("ace:ics")
    assert state is not None
    assert state.etag == '"v1"'
    assert state.last_status == 200
    assert state.last_success_at == RUN_1


def test_a_second_fetcher_against_a_reopened_database_gets_a_304(tmp_path):
    """The whole point of issue 0013, end to end and with ``fetch.py`` unchanged.

    Two feeds in this registry are 8 MB and 11 MB. Without a persistent store
    the ETag dies with the process and both are re-downloaded nightly for
    nothing.
    """
    db_path = tmp_path / "db" / "events.sqlite"
    router = Router().add(
        FEED_URL,
        reply(200, ICS_BODY, "text/calendar", ETag='"v1"'),
        reply(304, b"", ETag='"v1"'),
    )
    registry = make_registry()

    with Store(db_path) as store:
        first = next(make_fetcher(registry, router, tmp_path, store).fetch_all())
    assert first.outcome is Outcome.FETCHED
    assert first.conditional is False

    # A new process: new Store, new Fetcher, same database file.
    with Store(db_path) as reopened:
        second = next(make_fetcher(registry, router, tmp_path, reopened).fetch_all())
        state = reopened.get("ace:ics")

    assert second.outcome is Outcome.NOT_MODIFIED
    assert second.conditional is True
    assert second.status_code == 304
    assert second.body is None  # nothing to parse, nothing archived
    assert router.target_requests[1].headers["If-None-Match"] == '"v1"'
    assert state is not None and state.last_status == 304


def test_a_304_keeps_the_stored_content_hash(tmp_path):
    router = Router().add(
        FEED_URL,
        reply(200, ICS_BODY, "text/calendar", ETag='"v1"'),
        reply(304, b"", ETag='"v1"'),
    )
    registry = make_registry()
    with Store.in_memory() as store:
        next(make_fetcher(registry, router, tmp_path, store).fetch_all())
        after_fetch = store.get("ace:ics")
        next(make_fetcher(registry, router, tmp_path, store).fetch_all())
        after_304 = store.get("ace:ics")
    assert after_fetch is not None and after_304 is not None
    assert after_304.last_content_hash == after_fetch.last_content_hash
    assert after_304.consecutive_failures == 0


def test_last_modified_is_sent_back_as_if_modified_since(tmp_path):
    stamp = "Wed, 05 Aug 2026 03:15:00 GMT"
    router = Router().add(
        FEED_URL,
        reply(200, ICS_BODY, "text/calendar", **{"Last-Modified": stamp}),
        reply(304, b""),
    )
    registry = make_registry()
    with Store.in_memory() as store:
        next(make_fetcher(registry, router, tmp_path, store).fetch_all())
        next(make_fetcher(registry, router, tmp_path, store).fetch_all())
    assert router.target_requests[1].headers["If-Modified-Since"] == stamp


def test_a_failure_increments_consecutive_failures_in_sqlite(tmp_path):
    router = Router().add(FEED_URL, reply(503, b""))
    registry = make_registry()
    with Store.in_memory() as store:
        result = next(make_fetcher(registry, router, tmp_path, store).fetch_all())
        assert result.outcome is Outcome.FAILED
        state = store.get("ace:ics")
    assert state is not None and state.consecutive_failures == 1


# --------------------------------------------------------------------------- first_seen


def test_first_seen_is_preserved_when_content_hash_changes():
    """The regression this issue exists for.

    A space edits a title. ``content_hash`` moves, ``first_seen`` does not —
    otherwise issue 0018 republishes the event with a new ``pubDate`` and every
    subscriber is notified about an event they already have.
    """
    with Store.in_memory() as store:
        store.record_events([make_event(title="Open Shop Night")], now=RUN_1)

        edited = make_event(title="Open Shop Night (now with laser)", content_hash="hash-2")
        merge = store.record_events([edited], now=RUN_2)

        assert merge.changed_uids == ("ace:evt-1",)
        assert merge.new_uids == ()
        assert store.first_seen("ace:evt-1") == RUN_1
        assert merge.events[0].first_seen == RUN_1
        assert merge.events[0].last_seen == RUN_2

        stored = store.get_event("ace:evt-1")
        assert stored is not None
        assert stored.first_seen == RUN_1
        assert stored.content_hash == "hash-2"
        assert stored.title == "Open Shop Night (now with laser)"
        assert stored.last_changed_at == RUN_2


def test_first_seen_survives_repeated_content_changes():
    with Store.in_memory() as store:
        store.record_events([make_event(content_hash="h1")], now=RUN_1)
        store.record_events([make_event(content_hash="h2")], now=RUN_2)
        store.record_events([make_event(content_hash="h3")], now=RUN_3)
        assert store.first_seen("ace:evt-1") == RUN_1
        assert store.event_count() == 1


def test_first_seen_is_preserved_across_two_runs_where_the_event_is_unchanged():
    with Store.in_memory() as store:
        store.record_events([make_event()], now=RUN_1)
        merge = store.record_events([make_event()], now=RUN_2)

        assert merge.unchanged_uids == ("ace:evt-1",)
        assert merge.changed_uids == ()
        stored = store.get_event("ace:evt-1")
        assert stored is not None
        assert stored.first_seen == RUN_1
        assert stored.last_seen == RUN_2
        assert stored.last_changed_at == RUN_1  # nothing changed, so it did not move


def test_an_incoming_first_seen_never_overwrites_a_stored_one():
    # normalize.py sets first_seen to the run timestamp; the store is the
    # authority and must ignore it for a uid it already knows.
    with Store.in_memory() as store:
        store.record_events([make_event(first_seen=RUN_1)], now=RUN_1)
        store.record_events([make_event(first_seen=RUN_2, content_hash="h2")], now=RUN_2)
        assert store.first_seen("ace:evt-1") == RUN_1


def test_a_new_uid_takes_first_seen_from_the_run_timestamp():
    with Store.in_memory() as store:
        merge = store.record_events([make_event(first_seen=None)], now=RUN_2)
        assert merge.new_uids == ("ace:evt-1",)
        assert store.first_seen("ace:evt-1") == RUN_2


def test_record_events_classifies_new_changed_and_unchanged():
    with Store.in_memory() as store:
        store.record_events(
            [make_event("ace:a", content_hash="a1"), make_event("ace:b", content_hash="b1")],
            now=RUN_1,
        )
        merge = store.record_events(
            [
                make_event("ace:a", content_hash="a1"),
                make_event("ace:b", content_hash="b2"),
                make_event("ace:c", content_hash="c1"),
            ],
            now=RUN_2,
        )
        assert merge.new_uids == ("ace:c",)
        assert merge.changed_uids == ("ace:b",)
        assert merge.unchanged_uids == ("ace:a",)
        assert (merge.count, merge.new_count, merge.changed_count, merge.unchanged_count) == (
            3,
            1,
            1,
            1,
        )
        assert merge.recorded_at == RUN_2


def test_first_seen_of_an_unknown_uid_is_none():
    with Store.in_memory() as store:
        assert store.first_seen("ace:never") is None
        assert store.get_event("ace:never") is None


# --------------------------------------------------------------------------- carry-forward


def test_carry_forward_retrieves_the_last_known_good_events():
    with Store.in_memory() as store:
        store.record_events(
            [make_event("ace:a"), make_event("ace:b")],
            now=RUN_1,
        )
        events = store.carry_forward_events("ace", "ics")
        assert [event.uid for event in events] == ["ace:a", "ace:b"]
        assert store.last_known_good_at("ace", "ics") == RUN_1


def test_carry_forward_returns_only_the_most_recent_populated_run():
    # An event the source deliberately removed must not be resurrected.
    with Store.in_memory() as store:
        store.record_events([make_event("ace:a"), make_event("ace:cancelled")], now=RUN_1)
        store.record_events([make_event("ace:a"), make_event("ace:b")], now=RUN_2)
        assert [e.uid for e in store.carry_forward_events("ace", "ics")] == [
            "ace:a",
            "ace:b",
        ]
        # The removed event is still in history, just not carried forward.
        assert store.get_event("ace:cancelled") is not None
        assert len(store.events_for_source("ace", "ics")) == 3


def test_carry_forward_is_empty_for_a_source_that_never_returned_anything():
    with Store.in_memory() as store:
        assert store.carry_forward_events("humanmade", "eventbrite") == []
        assert store.last_known_good_at("humanmade", "eventbrite") is None


def test_carry_forward_events_round_trip_the_whole_event():
    rich = make_event(
        "ace:rich",
        location_name="Ace Makerspace",
        address="6050 Lowell St, Oakland, CA",
        url="https://ace.example.org/events/1",
        price="$45 / $30 members",
        description="Bring a project.",
        categories=("woodshop", "open-shop"),
        rrule="FREQ=WEEKLY;BYDAY=TU",
        source_uid="evt-1@ace",
        dtstart_form="tzid",
        recurring=True,
        multi_day=False,
        days=1,
        start_date=dt.date(2026, 8, 11),
        end_date=dt.date(2026, 8, 11),
        address_source=AddressSource.OVERRIDE,
        all_day=False,
    )
    with Store.in_memory() as store:
        store.record_events([rich], now=RUN_1)
        (back,) = store.carry_forward_events("ace", "ics")

    assert back.uid == rich.uid
    assert back.title == rich.title
    assert back.start_utc == rich.start_utc
    assert back.end_utc == rich.end_utc
    assert back.categories == ("woodshop", "open-shop")
    assert back.price == "$45 / $30 members"
    assert back.rrule == "FREQ=WEEKLY;BYDAY=TU"
    assert back.address_source is AddressSource.OVERRIDE
    assert back.start_date == dt.date(2026, 8, 11)
    assert back.recurring is True
    assert back.first_seen == RUN_1
    assert back.start_utc.tzinfo is not None  # never naive, even after a round trip


def test_carry_forward_keeps_sources_apart():
    with Store.in_memory() as store:
        store.record_events(
            [
                make_event("ace:a", space_id="ace", label="ics"),
                make_event("sudo:a", space_id="sudo-room", label="gcal_ics"),
            ],
            now=RUN_1,
        )
        assert [e.uid for e in store.carry_forward_events("ace", "ics")] == ["ace:a"]
        assert [e.uid for e in store.carry_forward_events("sudo-room", "gcal_ics")] == [
            "sudo:a"
        ]


def test_event_from_dict_restores_a_quarantined_event():
    event = make_event("ace:q", quarantine=QuarantineReason.EMPTY_TITLE)
    back = event_from_dict(event.as_dict())
    assert back.quarantine is QuarantineReason.EMPTY_TITLE
    assert back.is_quarantined


# --------------------------------------------------------------------- require_nonzero_once


def test_a_never_seen_source_has_never_returned_events():
    with Store.in_memory() as store:
        assert store.has_ever_returned_events("humanmade", "eventbrite") is False
        assert store.first_nonzero_at("humanmade", "eventbrite") is None


def test_a_previously_populated_source_has_returned_events():
    with Store.in_memory() as store:
        store.record_events([make_event()], now=RUN_1)
        assert store.has_ever_returned_events("ace", "ics") is True
        assert store.first_nonzero_at("ace", "ics") == RUN_1


def test_has_ever_returned_events_stays_true_after_a_zero_night():
    """"Went to zero" and "never non-zero" are different conditions.

    This is the distinction the ``require_nonzero_once`` gate rests on.
    """
    with Store.in_memory() as store:
        run_1 = store.start_run(RUN_1)
        store.record_source_run(run_1, FakeSourceRecord(event_count=7))
        run_2 = store.start_run(RUN_2)
        store.record_source_run(run_2, FakeSourceRecord(event_count=0, status="ok"))

        assert store.has_ever_returned_events("ace", "ics") is True
        assert store.first_nonzero_at("ace", "ics") == RUN_1


def test_a_source_that_is_zero_every_night_never_flips():
    # Humanmade's Eventbrite organizer, live: 0 upcoming, so a "went to zero"
    # gate can never fire and a naive alert fires nightly.
    with Store.in_memory() as store:
        for stamp in (RUN_1, RUN_2, RUN_3):
            run_id = store.start_run(stamp)
            store.record_source_run(
                run_id,
                FakeSourceRecord(space_id="humanmade", label="eventbrite", event_count=0),
            )
        assert store.has_ever_returned_events("humanmade", "eventbrite") is False


def test_first_nonzero_at_does_not_move_on_later_runs():
    with Store.in_memory() as store:
        run_1 = store.start_run(RUN_1)
        store.record_source_run(run_1, FakeSourceRecord(event_count=7))
        run_2 = store.start_run(RUN_2)
        store.record_source_run(run_2, FakeSourceRecord(event_count=9))
        assert store.first_nonzero_at("ace", "ics") == RUN_1


def test_sources_never_nonzero_reports_the_waiting_ones():
    with Store.in_memory() as store:
        store.record_events([make_event()], now=RUN_1)
        never = store.sources_never_nonzero(
            ["ace:ics", "humanmade:eventbrite", "hacker-dojo:luma"]
        )
        assert never == ["humanmade:eventbrite", "hacker-dojo:luma"]


# --------------------------------------------------------------------------- run history


def test_run_history_records_and_reads_back():
    with Store.in_memory() as store:
        run_id = store.start_run(RUN_1, horizon_days=120, version="0.1.0")
        store.record_source_run(run_id, FakeSourceRecord())
        store.finish_run(
            run_id,
            finished_at=RUN_1 + dt.timedelta(minutes=4),
            event_count=171,
            published=True,
            exit_code=0,
        )

        run = store.latest_run()
        assert run is not None
        assert run.run_id == run_id
        assert run.started_at == RUN_1
        assert run.elapsed_seconds == pytest.approx(240.0)
        assert run.event_count == 171
        assert run.published is True
        assert run.source_count == 1
        assert run.horizon_days == 120
        assert run.version == "0.1.0"
        assert run.exit_code == 0

        (source,) = store.sources_for_run(run_id)
        assert source.source_key == "ace:ics"
        assert source.http_status == 200
        assert source.content_type == "text/calendar"
        assert source.bytes == 8_400_000
        assert source.raw_count == 89
        assert source.horizon_count == 7  # gates count this one, not raw_count
        assert source.fetch_seconds == pytest.approx(1.5)
        assert source.elapsed_seconds == pytest.approx(2.25)
        assert source.started_at == RUN_1


def test_run_history_records_the_health_verdict():
    with Store.in_memory() as store:
        run_id = store.start_run(RUN_1)
        store.finish_run(
            run_id,
            finished_at=RUN_2,
            health_blocked=True,
            health_reasons=("global count dropped 61% night-over-night",),
            exit_code=3,
        )
        run = store.get_run(run_id)
        assert run is not None
        assert run.health_blocked is True
        assert run.health_reasons == ("global count dropped 61% night-over-night",)
        assert run.exit_code == 3


def test_gate_outcomes_are_recorded_per_source():
    with Store.in_memory() as store:
        run_id = store.start_run(RUN_1)
        store.record_source_run(
            run_id,
            FakeSourceRecord(event_count=0),
            gate_blocked=True,
            gate_reasons=["went to zero"],
        )
        (source,) = store.sources_for_run(run_id)
        assert source.gate_blocked is True
        assert source.gate_reasons == ("went to zero",)

        store.set_gate_outcome(run_id, "ace", "ics", blocked=False, reasons=["allow_zero"])
        (updated,) = store.sources_for_run(run_id)
        assert updated.gate_blocked is False
        assert updated.gate_reasons == ("allow_zero",)


def test_source_history_is_newest_first_for_night_over_night():
    with Store.in_memory() as store:
        for stamp, count in ((RUN_1, 7), (RUN_2, 6), (RUN_3, 2)):
            run_id = store.start_run(stamp)
            store.record_source_run(run_id, FakeSourceRecord(event_count=count))
        history = store.source_history("ace", "ics", limit=3)
        assert [row.event_count for row in history] == [2, 6, 7]
        assert [row.started_at for row in history] == [RUN_3, RUN_2, RUN_1]
        # 2 vs 6 is a 67% drop — the material issue 0016 gates on.
        assert history[0].event_count / history[1].event_count < 0.6


def test_runs_are_returned_newest_first_and_respect_the_limit():
    with Store.in_memory() as store:
        for stamp in (RUN_1, RUN_2, RUN_3):
            store.finish_run(store.start_run(stamp), finished_at=stamp)
        assert [run.started_at for run in store.runs(limit=2)] == [RUN_3, RUN_2]
        assert len(store.runs(limit=10)) == 3


def test_event_counts_for_run_keys_by_source():
    with Store.in_memory() as store:
        run_id = store.start_run(RUN_1)
        store.record_source_run(run_id, FakeSourceRecord(event_count=7))
        store.record_source_run(
            run_id,
            FakeSourceRecord(space_id="maker-nexus", label="ics", event_count=171),
        )
        assert store.event_counts_for_run(run_id) == {"ace:ics": 7, "maker-nexus:ics": 171}


def test_recording_the_same_source_twice_in_one_run_replaces_it():
    with Store.in_memory() as store:
        run_id = store.start_run(RUN_1)
        store.record_source_run(run_id, FakeSourceRecord(event_count=7))
        store.record_source_run(run_id, FakeSourceRecord(event_count=9))
        assert store.event_counts_for_run(run_id) == {"ace:ics": 9}


def test_record_run_writes_a_whole_report():
    class FakeHealth:
        blocked = True
        reasons = ("ace:ics went to zero",)

    class FakeReport:
        started_at = RUN_1
        finished_at = RUN_1 + dt.timedelta(minutes=3)
        dry_run = True
        space_filter = "ace"
        horizon_days = 120
        published = False
        exit_code = 3
        version = "0.1.0"
        health = FakeHealth()
        emit = None
        records = [FakeSourceRecord(event_count=7)]
        event_count = 7

    with Store.in_memory() as store:
        run_id = store.record_run(FakeReport())
        run = store.get_run(run_id)
        assert run is not None
        assert run.dry_run is True
        assert run.space_filter == "ace"
        assert run.event_count == 7
        assert run.health_blocked is True
        assert run.health_reasons == ("ace:ics went to zero",)
        assert store.event_counts_for_run(run_id) == {"ace:ics": 7}
        assert store.has_ever_returned_events("ace", "ics") is True


def test_get_run_returns_none_for_an_unknown_run():
    with Store.in_memory() as store:
        assert store.get_run(999) is None
        assert store.latest_run() is None


def test_events_can_be_tagged_with_the_run_that_recorded_them():
    with Store.in_memory() as store:
        run_id = store.start_run(RUN_1)
        store.record_events([make_event()], run_id=run_id, now=RUN_1)
        row = store.connection.execute(
            "SELECT run_id FROM event WHERE uid = ?", ("ace:evt-1",)
        ).fetchone()
        assert row["run_id"] == run_id
