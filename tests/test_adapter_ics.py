"""Tests for the ICS adapter (issue 0007).

**No test here touches the network.** Issue 0001 — the bot about page that
``$MAKER_CALENDAR_CONTACT`` points at — is still open, so a live fetch would go
out under a User-Agent naming a page that does not resolve. Every fixture under
``tests/fixtures/ics/`` is hand-authored instead of captured, and each one
isolates exactly one documented trap from the 2026-08-05 source survey.

``today`` is pinned to **2026-08-05**, the survey date. Nothing here depends on
the wall clock, so the RRULE expansion counts below are exact rather than
approximate.

Four things are being defended.

**Post-expansion counts.** ``vevent_count`` and ``event_count`` are different
numbers and the health gates depend on the difference: Sequoia Fabrica is 89 and
~7, Maker Nexus 3645 and 171, Sudo Room 5057 and 73.

**Bounded expansion.** Noisebridge's retired gCal has five RRULEs with no
``UNTIL``. It cannot be allowed to run away, and the bound must be the horizon
rather than luck.

**Every DTSTART form in the registry**, including bare-UTC ``Z`` and ``TZID=``
inside the same file, which Sequoia Fabrica genuinely does.

**"HTTP 200 is not success."** A ``text/html`` body is a reported failure — not
an exception, and never a silently empty list.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from pipeline.adapters.ics import (
    CALENDAR_MAGIC,
    DEFAULT_HORIZON_DAYS,
    IcsParse,
    IcsProblem,
    looks_like_calendar,
    parse,
    parse_ics,
    parse_ics_text,
)
from pipeline.fetch import FetchResult, Outcome

FIXTURES = Path(__file__).parent / "fixtures" / "ics"

#: The source-survey date. Every fixture is authored relative to it.
TODAY = dt.date(2026, 8, 5)
NOW = dt.datetime(2026, 8, 5, 3, 15, tzinfo=dt.timezone.utc)

#: today + 120 days. Events on 2026-12-02 are in; 2026-12-10 is out.
HORIZON_END = TODAY + dt.timedelta(days=DEFAULT_HORIZON_DAYS)


# --------------------------------------------------------------------------- helpers


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def parse_fixture(name: str, **kwargs: object) -> IcsParse:
    kwargs.setdefault("today", TODAY)
    kwargs.setdefault("now", NOW)
    return parse_ics_text(load(name), space_id="test-space", label=name, **kwargs)  # type: ignore[arg-type]


def fetched(
    body: str | bytes,
    *,
    content_type: str | None = "text/calendar",
    status_code: int = 200,
    outcome: Outcome = Outcome.FETCHED,
    charset: str | None = "utf-8",
) -> FetchResult:
    """A :class:`FetchResult` as issue 0006 would hand one to an adapter."""
    payload = body.encode("utf-8") if isinstance(body, str) else body
    return FetchResult(
        space_id="test-space",
        label="ics",
        adapter="ics",
        url="https://example.test/?ical=1",
        outcome=outcome,
        status_code=status_code,
        content_type=content_type,
        body=payload,
        byte_count=len(payload),
        charset=charset,
    )


EMPTY_CALENDAR = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//empty//EN\r\nEND:VCALENDAR\r\n"


# --------------------------------------------------------------------------- all-day


def test_value_date_all_day_event_is_flagged_and_dated():
    result = parse_fixture("allday-value-date.ics")

    assert result.ok
    assert result.event_count == 2
    event = result.events[0]
    assert event.all_day is True
    assert event.dtstart_form == "date"
    assert event.start == dt.date(2026, 8, 20)
    assert isinstance(event.start, dt.date) and not isinstance(event.start, dt.datetime)
    assert event.tz is None
    assert event.source_tz is None


def test_all_day_dtend_is_exclusive_in_the_feed_and_inclusive_in_our_output():
    # The fixture says DTEND;VALUE=DATE:20260821 for a one-day event on the 20th.
    # Carrying that through unchanged makes every all-day event a day too long.
    assert "DTEND;VALUE=DATE:20260821" in load("allday-value-date.ics")

    event = parse_fixture("allday-value-date.ics").events[0]

    assert event.end == dt.date(2026, 8, 20)
    assert event.days == 1
    assert event.multi_day is False
    assert event.dtend_exclusive == dt.date(2026, 8, 21)


def test_all_day_without_dtend_is_a_single_day():
    event = parse_fixture("allday-value-date.ics").events[1]

    assert event.uid == "allday-no-dtend@theboxshop.test"
    assert event.all_day is True
    assert event.start == dt.date(2026, 8, 27)
    assert event.end == dt.date(2026, 8, 27)
    assert event.days == 1


# --------------------------------------------------------------------------- DTSTART forms


def test_floating_time_with_no_tzid_is_flagged_and_left_naive():
    # No TZID and no X-WR-TIMEZONE: there is nothing to resolve it against here.
    # normalize.py (issue 0009) applies the space timezone and is where a naive
    # datetime becomes a hard error; the adapter's job is to say so honestly.
    result = parse_fixture("floating-no-tzid.ics")

    assert result.ok
    event = result.events[0]
    assert event.dtstart_form == "floating"
    assert event.tz is None
    assert event.source_tz is None
    assert event.start == dt.datetime(2026, 8, 12, 19, 0)
    assert event.start.tzinfo is None


def test_bare_utc_and_tzid_forms_coexist_in_one_feed():
    # Sequoia Fabrica's calendar genuinely mixes these two.
    text = load("sequoia-mixed-dtstart.ics")
    assert "DTSTART:20260813T020000Z" in text
    assert "DTSTART;TZID=America/Los_Angeles:20260818T190000" in text

    result = parse_fixture("sequoia-mixed-dtstart.ics")

    assert result.ok
    assert result.event_count == 2
    by_uid = {event.uid: event for event in result.events}

    bare = by_uid["bare-utc@sequoiafabrica.test"]
    assert bare.dtstart_form == "utc"
    assert bare.source_tz == "UTC"

    zoned = by_uid["tzid@sequoiafabrica.test"]
    assert zoned.dtstart_form == "tzid"
    assert zoned.source_tz == "America/Los_Angeles"
    assert zoned.start == dt.datetime(
        2026, 8, 18, 19, 0, tzinfo=zoned.start.tzinfo
    )


def test_x_wr_timezone_rewrites_the_bare_utc_instant_but_the_source_form_survives():
    # recurring-ical-events applies X-WR-TIMEZONE, so a bare-UTC DTSTART comes
    # back already converted to the calendar's zone. The instant is preserved --
    # 2026-08-13T02:00Z is 2026-08-12T19:00-07:00 -- but the evidence of which
    # form the feed used would be erased if we read tz off the expanded value.
    # Issue 0009 is asked to "carry the original tz string", so dtstart_form and
    # source_tz are read from the unexpanded VEVENT instead.
    event = {e.uid: e for e in parse_fixture("sequoia-mixed-dtstart.ics")}[
        "bare-utc@sequoiafabrica.test"
    ]

    assert event.tz == "America/Los_Angeles"  # after X-WR-TIMEZONE correction
    assert event.source_tz == "UTC"  # what the feed actually wrote
    assert event.start.utcoffset() == dt.timedelta(hours=-7)
    assert event.start.astimezone(dt.timezone.utc) == dt.datetime(
        2026, 8, 13, 2, 0, tzinfo=dt.timezone.utc
    )


# --------------------------------------------------------------------------- RRULEs


def test_unbounded_rrule_expands_to_a_bounded_horizon_clipped_count():
    """The one that matters.

    Five RRULEs with no UNTIL on a calendar dead since 2024-01. Unbounded
    expansion would not terminate; the horizon is the bound.
    """
    result = parse_fixture("noisebridge-unbounded-rrule.ics")

    assert result.ok
    assert result.vevent_count == 7
    assert result.recurring_vevent_count == 6
    # 3 weekly (17 each) + 1st-Wednesday (5) + 2nd-Thursday (4) over 120 days.
    assert result.event_count == 60

    for event in result.events:
        start = event.start.date() if isinstance(event.start, dt.datetime) else event.start
        assert TODAY <= start <= HORIZON_END, f"{event.uid} escaped the horizon"


def test_unbounded_rrule_count_scales_with_the_horizon_and_never_runs_away():
    short = parse_fixture("noisebridge-unbounded-rrule.ics", horizon_days=30)
    default = parse_fixture("noisebridge-unbounded-rrule.ics")
    long = parse_fixture("noisebridge-unbounded-rrule.ics", horizon_days=365)

    assert short.event_count == 15
    assert default.event_count == 60
    assert long.event_count == 181
    assert short.event_count < default.event_count < long.event_count


def test_unbounded_rrule_is_still_bounded_five_years_later():
    # The failure mode is not "many events", it is "a generator with no stop".
    # Moving today forward five years must not change the shape of the answer.
    future = parse_fixture(
        "noisebridge-unbounded-rrule.ics",
        today=dt.date(2031, 8, 5),
        now=dt.datetime(2031, 8, 5, 3, 15, tzinfo=dt.timezone.utc),
    )

    assert future.ok
    assert 40 <= future.event_count <= 80
    assert future.vevent_count == 7
    # Still fabricating, still dated 2024. This is what max_stale_days catches.
    assert future.last_modified == dt.datetime(2024, 6, 13, tzinfo=dt.timezone.utc)
    assert future.stale_days is not None and future.stale_days > 2500


def test_rrule_with_an_until_in_the_past_produces_nothing():
    result = parse_fixture("noisebridge-unbounded-rrule.ics")

    assert "UNTIL=20231225T235959Z" in load("noisebridge-unbounded-rrule.ics")
    assert not [e for e in result.events if e.uid == "expired-with-until@noisebridge.test"]


def test_recurring_occurrences_share_a_uid_and_are_told_apart_by_instance_key():
    result = parse_fixture("noisebridge-unbounded-rrule.ics")

    weekly = [e for e in result.events if e.uid == "pyclass@noisebridge.test"]
    assert len(weekly) == 17
    assert all(e.recurring is True for e in weekly)
    assert len({e.uid for e in weekly}) == 1
    assert len({e.instance_key for e in weekly}) == 17


# --------------------------------------------------------------------------- clipping


def test_preexpanded_recurrences_outside_the_horizon_are_clipped():
    # Sudo Room's export carries no RRULEs at all -- it materializes weekly
    # events as individual VEVENTs from 2012 through 2058.
    result = parse_fixture("sudoroom-preexpanded.ics")

    assert result.ok
    assert result.recurring_vevent_count == 0
    assert result.vevent_count == 6
    assert result.event_count == 3

    kept = {e.uid for e in result.events}
    assert "em-2012-0111@sudoroom.test" not in kept  # 14 years in the past
    assert "em-2058-0219@sudoroom.test" not in kept  # 32 years in the future


def test_the_horizon_boundary_includes_the_last_day_and_excludes_the_next():
    result = parse_fixture("sudoroom-preexpanded.ics")
    kept = {e.uid for e in result.events}

    assert "em-2026-1125@sudoroom.test" in kept  # 2026-11-26, inside 120 days
    assert "em-2026-1210@sudoroom.test" not in kept  # 2026-12-11, outside
    assert result.window_start == TODAY
    assert result.window_end == HORIZON_END


def test_vevent_count_and_event_count_are_reported_as_different_numbers():
    # "Count post-expansion events inside the horizon, not VEVENTs."
    preexpanded = parse_fixture("sudoroom-preexpanded.ics")
    recurring = parse_fixture("noisebridge-unbounded-rrule.ics")

    assert preexpanded.vevent_count > preexpanded.event_count  # 6 -> 3
    assert recurring.vevent_count < recurring.event_count  # 7 -> 60


# --------------------------------------------------------------------------- Luma


def test_luma_multi_day_all_day_event_is_normalized():
    # Luma exports a multi-day event as an all-day span. The times are gone and
    # cannot be recovered; what we can do is make the span honest and label it,
    # so it does not render a day too long above every timed event.
    text = load("luma-multiday.ics")
    assert "DTSTART;VALUE=DATE:20260810" in text
    assert "DTEND;VALUE=DATE:20260813" in text  # exclusive: the 13th is not in it

    event = {e.uid: e for e in parse_fixture("luma-multiday.ics")}[
        "evt-multiday@events.lu.ma"
    ]

    assert event.all_day is True
    assert event.multi_day is True
    assert event.days == 3
    assert event.start == dt.date(2026, 8, 10)
    assert event.end == dt.date(2026, 8, 12)  # inclusive last day, not the 13th
    assert event.dtend_exclusive == dt.date(2026, 8, 13)
    assert event.url == "https://luma.com/frontier-hackathon"


def test_luma_single_day_all_day_event_is_not_marked_multi_day():
    event = {e.uid: e for e in parse_fixture("luma-multiday.ics")}[
        "evt-singleday@events.lu.ma"
    ]

    assert event.all_day is True
    assert event.multi_day is False
    assert event.days == 1
    assert event.start == event.end == dt.date(2026, 8, 15)


def test_luma_timed_event_is_untouched_by_the_all_day_normalization():
    event = {e.uid: e for e in parse_fixture("luma-multiday.ics")}["evt-timed@events.lu.ma"]

    assert event.all_day is False
    assert event.multi_day is False
    assert event.dtstart_form == "utc"
    assert event.start == dt.datetime(2026, 8, 22, 1, 0, tzinfo=event.start.tzinfo)
    assert event.organizer == "Laurence Ion"  # CN, not the anonymous mailto


# --------------------------------------------------------------------------- UIDs


def test_the_source_uid_is_preserved_verbatim():
    # normalize.py namespaces this as {space_id}:{source_uid}. If the adapter
    # rewrites or synthesizes it, every subscriber sees every event as new every
    # night -- and nothing downstream can tell that happened.
    result = parse_fixture("ace-tribe.ics")

    uids = [event.uid for event in result.events]
    assert uids == [
        "36521-20260811T180000-20260811T210000@www.acemakerspace.org",
        "36988-20260903T010000-20260903T040000@www.acemakerspace.org",
    ]
    for uid in uids:
        assert uid in load("ace-tribe.ics")


def test_the_adapter_does_not_namespace_uids_or_normalize_timezones():
    # The seam with issue 0009, asserted rather than assumed.
    result = parse_fixture("ace-tribe.ics")

    for event in result.events:
        assert not event.uid.startswith("test-space:")
        assert event.start.tzinfo is not None
        assert str(event.start.tzinfo) != "UTC"  # still in the feed's own zone


# --------------------------------------------------------------------------- staleness


def test_last_modified_is_surfaced_for_the_staleness_gate():
    result = parse_fixture("ace-tribe.ics")

    assert result.last_modified == dt.datetime(2026, 8, 4, 16, 45, tzinfo=dt.timezone.utc)
    assert result.last_change == result.last_modified
    assert result.stale_days is not None
    assert 0 < result.stale_days < 1


def test_last_modified_scans_vevents_outside_the_horizon():
    # The whole point of the gate. Noisebridge's newest LAST-MODIFIED is
    # 2024-06-13 while its unbounded RRULEs keep emitting events dated next
    # week. Scanning only the expanded window would report a fresh feed.
    result = parse_fixture("noisebridge-unbounded-rrule.ics")

    assert result.event_count == 60
    assert all(e.start.year == 2026 for e in result.events)
    assert result.last_modified == dt.datetime(2024, 6, 13, tzinfo=dt.timezone.utc)
    assert result.stale_days is not None and result.stale_days > 780


def test_dtstamp_is_the_fallback_when_the_feed_has_no_last_modified():
    result = parse_fixture("floating-no-tzid.ics")

    assert result.last_modified is None
    assert result.dtstamp == dt.datetime(2026, 8, 1, 9, 0, tzinfo=dt.timezone.utc)
    assert result.last_change == result.dtstamp


def test_stale_days_is_none_rather_than_zero_when_the_feed_dates_nothing():
    undated = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//undated//EN\r\n"
        "BEGIN:VEVENT\r\nUID:u@x\r\nSUMMARY:No stamps\r\n"
        "DTSTART:20260901T170000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    result = parse_ics_text(undated, today=TODAY, now=NOW)

    assert result.ok
    assert result.event_count == 1
    assert result.last_change is None
    assert result.stale_days is None  # "undated" is not the same as "fresh"


# --------------------------------------------------------------------------- fields


def test_canonical_fields_available_at_this_stage_are_carried():
    event = parse_fixture("ace-tribe.ics").events[0]

    assert event.title == "Laser Cutter Basics"
    assert event.location == "6050 Lowell St, Oakland, CA 94608"
    assert event.description == "Required before using the laser cutters."
    assert event.url == "https://www.acemakerspace.org/event/laser-cutter-basics/"
    assert event.categories == ("Classes", "Laser Cutting")
    assert event.status == "CONFIRMED"
    assert event.sequence == 0
    assert event.last_modified == dt.datetime(2026, 8, 4, 16, 45, tzinfo=dt.timezone.utc)


def test_repeated_categories_lines_are_flattened():
    event = parse_fixture("ace-tribe.ics").events[1]

    assert "CATEGORIES:Open Studio" in load("ace-tribe.ics")
    assert "CATEGORIES:Textiles" in load("ace-tribe.ics")
    assert event.categories == ("Open Studio", "Textiles")


def test_feed_metadata_is_surfaced_for_the_gcal_wrapper():
    # Issue 0008 needs X-WR-CALNAME / X-WR-CALDESC; Sequoia Fabrica's CALDESC is
    # what proved its gCal is disjoint from its Bookwhen calendar.
    result = parse_fixture("sequoia-mixed-dtstart.ics")

    assert result.calendar_name == "Sequoia Fabrica - Community Calendar"
    assert result.calendar_description is not None
    assert "Bookwhen" in result.calendar_description
    assert result.calendar_timezone == "America/Los_Angeles"
    assert result.prodid is not None and "Google" in result.prodid


def test_refresh_interval_is_read_from_either_spelling():
    assert parse_fixture("luma-multiday.ics").refresh_interval == "PT12H"
    assert parse_fixture("ace-tribe.ics").refresh_interval == "PT1H"


def test_events_come_back_sorted_by_start():
    result = parse_fixture("noisebridge-unbounded-rrule.ics")

    starts = [
        e.start.astimezone(dt.timezone.utc) if isinstance(e.start, dt.datetime) else e.start
        for e in result.events
    ]
    assert starts == sorted(starts)


# --------------------------------------------------------------------------- HTTP 200 is not success


def test_html_body_is_reported_as_a_clear_failure_not_an_exception():
    # A 200 with text/html. This must not raise, and must not look like an
    # empty calendar -- an empty calendar is a legitimate state for The Box Shop.
    result = parse_ics(fetched(load("homepage.html"), content_type="text/html"), today=TODAY)

    assert result.ok is False
    assert bool(result) is False
    assert result.problem is IcsProblem.WRONG_CONTENT_TYPE
    assert result.events == ()
    assert result.error is not None
    assert "text/html" in result.error
    assert "text/calendar" in result.error


def test_html_body_failure_is_distinguishable_from_an_empty_calendar():
    html = parse_ics(fetched(load("homepage.html"), content_type="text/html"), today=TODAY)
    empty = parse_ics(fetched(EMPTY_CALENDAR), today=TODAY)

    assert html.event_count == empty.event_count == 0
    assert html.ok is False
    assert empty.ok is True  # 200, valid VCALENDAR, nothing upcoming
    assert empty.problem is IcsProblem.NONE


def test_a_calendar_content_type_over_a_homepage_body_is_still_a_failure():
    # ?ical=1 returning a byte-identical copy of the homepage. The header alone
    # would wave this through, so the body is sniffed as well.
    result = parse_ics(fetched(load("homepage.html"), content_type="text/calendar"), today=TODAY)

    assert result.ok is False
    assert result.problem is IcsProblem.NOT_CALENDAR
    assert CALENDAR_MAGIC in (result.error or "")


def test_a_valid_calendar_served_as_text_html_is_reported_not_parsed():
    # ?format=ical returning 200 with text/html, live in this registry. The body
    # happens to be fine; reporting the drift still beats quietly parsing it.
    result = parse_ics(fetched(load("ace-tribe.ics"), content_type="text/html"), today=TODAY)

    assert result.ok is False
    assert result.problem is IcsProblem.WRONG_CONTENT_TYPE
    assert result.events == ()


def test_text_plain_is_tolerated_when_the_body_really_is_a_calendar():
    result = parse_ics(fetched(load("ace-tribe.ics"), content_type="text/plain"), today=TODAY)

    assert result.ok is True
    assert result.event_count == 2
    assert result.error is not None and "tolerated" in result.error


def test_a_missing_content_type_with_a_calendar_body_is_accepted():
    result = parse_ics(fetched(load("ace-tribe.ics"), content_type=None), today=TODAY)

    assert result.ok is True
    assert result.event_count == 2


def test_strict_content_type_can_be_turned_off_but_the_body_check_stays():
    lenient = parse_ics(
        fetched(load("ace-tribe.ics"), content_type="text/html"),
        today=TODAY,
        strict_content_type=False,
    )
    assert lenient.ok is True

    still_html = parse_ics(
        fetched(load("homepage.html"), content_type="text/html"),
        today=TODAY,
        strict_content_type=False,
    )
    assert still_html.ok is False
    assert still_html.problem is IcsProblem.NOT_CALENDAR


# --------------------------------------------------------------------------- transport


def test_a_transport_failure_is_reported_not_raised():
    result = parse_ics(fetched("", outcome=Outcome.FAILED, status_code=503), today=TODAY)

    assert result.ok is False
    assert result.problem is IcsProblem.TRANSPORT
    assert result.events == ()


def test_a_304_is_its_own_problem_so_it_is_not_mistaken_for_zero():
    # Issue 0014 must carry yesterday's events forward here, not publish zero.
    result = parse_ics(fetched("", outcome=Outcome.NOT_MODIFIED, status_code=304), today=TODAY)

    assert result.ok is False
    assert result.problem is IcsProblem.NOT_MODIFIED
    assert "304" in (result.error or "")


def test_a_blocked_source_is_reported_as_transport():
    result = parse_ics(fetched("", outcome=Outcome.BLOCKED, status_code=None), today=TODAY)

    assert result.problem is IcsProblem.TRANSPORT


def test_an_empty_body_is_reported():
    result = parse_ics(fetched(b""), today=TODAY)

    assert result.ok is False
    assert result.problem is IcsProblem.EMPTY_BODY


def test_unparseable_bytes_are_reported_not_raised():
    result = parse_ics_text(
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nDTSTART:not-a-date\r\n",
        today=TODAY,
        now=NOW,
    )

    assert result.ok is False
    assert result.problem is IcsProblem.UNPARSEABLE
    assert result.events == ()


# --------------------------------------------------------------------------- shape


def test_parse_result_behaves_like_the_list_of_events_it_wraps():
    result = parse_fixture("ace-tribe.ics")

    assert len(result) == 2
    assert list(result) == list(result.events)
    assert result[0].uid.startswith("36521-")


def test_truthiness_is_the_verdict_and_not_the_count():
    empty = parse_ics_text(EMPTY_CALENDAR, today=TODAY, now=NOW)

    assert empty.ok is True
    assert bool(empty) is True
    assert len(empty) == 0


def test_events_convert_to_plain_dicts():
    event = parse_fixture("ace-tribe.ics").events[0]
    as_dict = event.as_dict()

    assert as_dict["uid"] == event.uid
    assert as_dict["title"] == "Laser Cutter Basics"
    assert as_dict["categories"] == ("Classes", "Laser Cutting")


def test_parse_is_the_registered_entry_point_alias():
    assert parse is parse_ics


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("BEGIN:VCALENDAR\r\nEND:VCALENDAR", True),
        ("﻿BEGIN:VCALENDAR\r\n", True),
        ("\r\n  begin:vcalendar\r\n", True),
        ("<!DOCTYPE html>", False),
        ("", False),
        ("x" * 8000 + "BEGIN:VCALENDAR", False),  # not in the first 4 KB
    ],
)
def test_looks_like_calendar(body: str, expected: bool):
    assert looks_like_calendar(body) is expected
