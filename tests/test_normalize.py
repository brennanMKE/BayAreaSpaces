"""Tests for normalize (issue 0009).

**No test here touches the network.** Same rule as the adapter tests: issue 0001
(the bot about page ``$MAKER_CALENDAR_CONTACT`` names) is still open, so nothing
in this suite may cause a request to go out. Every input is hand-authored.

``now`` is pinned to **2026-08-05T03:15Z** — the source-survey date at the hour
launchd actually runs the job — so every window calculation below is exact
rather than approximate.

Four things are being defended, and three of them are invariants from CLAUDE.md.

**No naive datetime survives.** Python will happily do arithmetic on a naive
datetime and produce an answer that is wrong by 7 or 8 hours depending on the
month. The assertion in ``normalize`` is the only thing standing between this
project and that, so it is tested from both sides: what must raise, and what
must be converted instead of raising.

**17 occurrences of one series get 17 UIDs.** This is the regression the whole
UID correction exists to prevent. Namespacing on ``{space_id}:{source_uid}``
alone collapses a series into a single event and presents as a working feed
with suspiciously few events — never as an error. It is tested end-to-end
through the real ICS adapter, because the flag that distinguishes the two cases
(``recurring``) is read off the unexpanded VEVENT and a hand-built ``RawEvent``
could not prove that path works.

**UID stability.** The same event normalized on two different nights, by two
differently-configured runs, must produce byte-identical UIDs. If it does not,
every subscriber sees every event as new every night.

**An override must not relocate a real event.** Sudo Room ships a stale address
on every event *and* genuinely holds events at The Box Shop in San Francisco.
Publishing the wrong address confidently is worse than publishing none.
"""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pipeline.adapters.ics import parse_ics_text
from pipeline.config import Space
from pipeline.normalize import (
    DEFAULT_TIMEZONE,
    MAX_TITLE_CHARS,
    AddressSource,
    DropReason,
    Event,
    NaiveDatetimeError,
    NormalizeError,
    QuarantineReason,
    RawEvent,
    content_hash,
    day_start_utc,
    from_ics_event,
    make_uid,
    normalize_event,
    normalize_events,
    normalize_ics,
    normalize_title,
    to_utc,
)

FIXTURES = Path(__file__).parent / "fixtures" / "ics"

#: The source-survey date, at the hour launchd runs the job.
NOW = dt.datetime(2026, 8, 5, 3, 15, tzinfo=dt.timezone.utc)
TODAY = dt.date(2026, 8, 5)

PACIFIC = "America/Los_Angeles"


# --------------------------------------------------------------------------- helpers


def sudo_room(**kwargs: object) -> Space:
    """Sudo Room as ``sources.yaml`` has it: a stale house address on every event."""
    fields: dict[str, object] = {
        "id": "sudo-room",
        "name": "Sudo Room",
        "city": "Oakland",
        "region": "east-bay",
        "url": "https://sudoroom.org/",
        "address_override": "Omni Commons, 4799 Shattuck Ave, Oakland, CA 94609",
    }
    fields.update(kwargs)
    return Space(**fields)  # type: ignore[arg-type]


def hacker_dojo() -> Space:
    """Hacker Dojo: its Meetup iCal has no ``LOCATION`` field at all."""
    return Space(
        id="hacker-dojo",
        name="Hacker Dojo",
        city="Mountain View",
        region="peninsula",
        url="https://hackerdojo.org/",
        address_override="855 Maude Ave, Mountain View, CA 94043",
    )


def noisebridge() -> Space:
    """A space with no override — its feeds carry real addresses."""
    return Space(
        id="noisebridge",
        name="Noisebridge",
        city="San Francisco",
        region="san-francisco",
        url="https://noisebridge.net/",
    )


def raw(**kwargs: object) -> RawEvent:
    """A minimal aware, timed :class:`RawEvent` with fields overridden."""
    fields: dict[str, object] = {
        "source_uid": "event-1@sudoroom.test",
        "title": "Sudo Sesh",
        "start": dt.datetime(2026, 9, 1, 19, 0, tzinfo=dt.timezone.utc),
        "end": dt.datetime(2026, 9, 1, 21, 0, tzinfo=dt.timezone.utc),
        "source_tz": "UTC",
        "dtstart_form": "utc",
    }
    fields.update(kwargs)
    return RawEvent(**fields)  # type: ignore[arg-type]


def one(event: RawEvent, *, space: Space | None = None, **kwargs: object) -> Event:
    """Normalize a single event and return it, failing loudly if it was not kept."""
    result = normalize_events(
        [event],
        space=space or sudo_room(),
        source_label="luma",
        now=NOW,
        **kwargs,  # type: ignore[arg-type]
    )
    assert result.event_count == 1, f"expected 1 event, got {result}"
    return result[0]


def series_ics(count: int = 17) -> str:
    """One weekly series, ``count`` occurrences, all sharing a single ``UID``.

    Modelled on the Sudo Room series named in CLAUDE.md. The horizon is 120
    days from 2026-08-05, so 17 weekly occurrences from 2026-08-06 all land
    inside it (the last is 2026-11-26) and the expansion count is exact.
    """
    return "\r\n".join(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Events Manager//sudoroom//EN",
            "CALSCALE:GREGORIAN",
            "X-WR-CALNAME:Sudo Room",
            "BEGIN:VEVENT",
            "UID:weekly-hack-night@sudoroom.test",
            "SUMMARY:Sudo Room Hack Night",
            "DTSTART;TZID=America/Los_Angeles:20260806T190000",
            "DTEND;TZID=America/Los_Angeles:20260806T220000",
            f"RRULE:FREQ=WEEKLY;BYDAY=TH;COUNT={count}",
            "DTSTAMP:20260801T000000Z",
            "LAST-MODIFIED:20260801T000000Z",
            "LOCATION:549 48th St, Oakland, CA 94609",
            "END:VEVENT",
            "BEGIN:VEVENT",
            "UID:one-off-soldering@sudoroom.test",
            "SUMMARY:Soldering Basics",
            "DTSTART;TZID=America/Los_Angeles:20260815T130000",
            "DTEND;TZID=America/Los_Angeles:20260815T160000",
            "DTSTAMP:20260801T000000Z",
            "LOCATION:549 48th St, Oakland, CA 94609",
            "END:VEVENT",
            "END:VCALENDAR",
            "",
        ]
    )


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- naive


def test_naive_datetime_raises_in_to_utc() -> None:
    """The enforcement point. No default zone, ever."""
    with pytest.raises(NaiveDatetimeError) as excinfo:
        to_utc(dt.datetime(2026, 9, 1, 19, 0))
    assert "naive" in str(excinfo.value)


def test_to_utc_converts_an_aware_datetime() -> None:
    pacific = dt.datetime(2026, 9, 1, 19, 0, tzinfo=ZoneInfo(PACIFIC))
    assert to_utc(pacific) == dt.datetime(2026, 9, 2, 2, 0, tzinfo=dt.timezone.utc)


def test_day_start_utc_anchors_at_local_midnight_not_utc_midnight() -> None:
    """August in the Bay Area is UTC-7, so local midnight is 07:00 UTC."""
    assert day_start_utc(dt.date(2026, 8, 20), PACIFIC) == dt.datetime(
        2026, 8, 20, 7, 0, tzinfo=dt.timezone.utc
    )
    # And UTC-8 in January — the offset is not a constant, which is the point.
    assert day_start_utc(dt.date(2027, 1, 20), PACIFIC) == dt.datetime(
        2027, 1, 20, 8, 0, tzinfo=dt.timezone.utc
    )


def test_naive_datetime_while_declaring_a_zone_raises() -> None:
    """A contradiction, not a floating time.

    ``source_tz`` says the zone is known and the value says it is not. Both
    cannot be true, and picking one is the guess the invariant forbids.
    """
    event = raw(
        start=dt.datetime(2026, 9, 1, 19, 0),
        source_tz=PACIFIC,
        dtstart_form="tzid",
    )
    with pytest.raises(NaiveDatetimeError) as excinfo:
        normalize_events([event], space=sudo_room(), now=NOW)
    assert "cannot both be true" in str(excinfo.value)


def test_naive_now_raises() -> None:
    with pytest.raises(NaiveDatetimeError):
        normalize_events([raw()], space=sudo_room(), now=dt.datetime(2026, 8, 5, 3, 15))


def test_a_naive_datetime_cannot_be_stored_on_the_record() -> None:
    """The last chokepoint, tested directly rather than through a path."""
    with pytest.raises(NaiveDatetimeError):
        Event(
            uid="sudo-room:x",
            space_id="sudo-room",
            source_label="luma",
            title="Hack Night",
            start_utc=dt.datetime(2026, 9, 1, 19, 0),  # naive
            end_utc=None,
            tz=PACIFIC,
        )


# --------------------------------------------------------------------------- floating


def test_floating_time_is_converted_through_the_registry_timezone_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Policy: interpret in the source's declared zone, never reject — but loudly.

    Issue 0007 passes floating times through naive on purpose so they arrive
    here. 19:00 floating in ``America/Los_Angeles`` is 02:00 UTC the next day.
    """
    caplog.set_level(logging.WARNING, logger="pipeline.normalize")
    event = one(
        raw(start=dt.datetime(2026, 9, 1, 19, 0), source_tz=None, dtstart_form="floating")
    )

    assert event.start_utc == dt.datetime(2026, 9, 2, 2, 0, tzinfo=dt.timezone.utc)
    assert event.tz == DEFAULT_TIMEZONE
    assert event.tz_assumed is True
    assert "floating time" in caplog.text
    assert DEFAULT_TIMEZONE in caplog.text


def test_floating_conversion_is_recorded_on_the_result() -> None:
    """Logging alone is not enough — the run report has to be able to count these."""
    result = normalize_events(
        [raw(start=dt.datetime(2026, 9, 1, 19, 0), source_tz=None, dtstart_form="floating")],
        space=sudo_room(),
        source_label="luma",
        now=NOW,
    )
    assert len(result.conversions) == 1
    conversion = result.conversions[0]
    assert conversion.zone == DEFAULT_TIMEZONE
    assert conversion.origin == "registry timezone"
    assert conversion.local == "2026-09-01T19:00:00"


def test_floating_time_prefers_the_calendar_timezone() -> None:
    """``X-WR-TIMEZONE`` is the calendar's own statement and outranks the default."""
    event = one(
        raw(start=dt.datetime(2026, 9, 1, 19, 0), source_tz=None, dtstart_form="floating"),
        calendar_timezone="America/New_York",
    )
    assert event.tz == "America/New_York"
    assert event.start_utc == dt.datetime(2026, 9, 1, 23, 0, tzinfo=dt.timezone.utc)


def test_floating_time_can_be_made_a_hard_failure() -> None:
    """The operator lever: lose the event rather than publish a shifted one."""
    with pytest.raises(NaiveDatetimeError):
        normalize_events(
            [raw(start=dt.datetime(2026, 9, 1, 19, 0), source_tz=None, dtstart_form="floating")],
            space=sudo_room(),
            now=NOW,
            allow_floating=False,
        )


def test_floating_fixture_normalizes_end_to_end() -> None:
    """The real adapter path, on the fixture issue 0007 wrote for this case."""
    parse = parse_ics_text(load("floating-no-tzid.ics"), space_id="humanmade", today=TODAY, now=NOW)
    result = normalize_ics(parse, space=noisebridge(), source_label="floating", now=NOW)

    assert result.event_count == 1
    event = result[0]
    assert event.tz_assumed is True
    # 19:00 floating, read as Pacific, is 02:00 UTC the next day.
    assert event.start_utc == dt.datetime(2026, 8, 13, 2, 0, tzinfo=dt.timezone.utc)
    assert len(result.conversions) == 1


# --------------------------------------------------------------------------- all-day


def test_all_day_date_is_anchored_not_coerced() -> None:
    """A ``date`` has no time and no zone; midnight *local* is the anchor.

    Treating it as midnight UTC would shift every all-day event in this
    registry by 7 hours and land a fair number on the wrong day.
    """
    event = one(
        raw(
            start=dt.date(2026, 8, 20),
            end=dt.date(2026, 8, 20),
            all_day=True,
            dtstart_form="date",
            source_tz=None,
        )
    )
    assert event.all_day is True
    assert event.start_date == dt.date(2026, 8, 20)
    assert event.end_date == dt.date(2026, 8, 20)
    assert event.start_utc == dt.datetime(2026, 8, 20, 7, 0, tzinfo=dt.timezone.utc)
    # Exclusive instant end: midnight starting the following day.
    assert event.end_utc == dt.datetime(2026, 8, 21, 7, 0, tzinfo=dt.timezone.utc)
    assert event.start_local.date() == dt.date(2026, 8, 20)


def test_all_day_fixture_keeps_both_forms_distinct() -> None:
    parse = parse_ics_text(load("allday-value-date.ics"), space_id="the-box-shop", today=TODAY, now=NOW)
    result = normalize_ics(parse, space=noisebridge(), source_label="squarespace", now=NOW)

    assert result.event_count == 2
    for event in result:
        assert event.all_day is True
        assert event.start_date is not None
        assert event.start_utc.tzinfo is not None
        assert event.start_utc.hour == 7  # midnight Pacific in August


def test_all_day_flag_with_a_datetime_is_an_error() -> None:
    """Never guess which of the two forms the adapter meant."""
    with pytest.raises(NormalizeError):
        normalize_events(
            [raw(start=dt.datetime(2026, 8, 20, 9, 0, tzinfo=dt.timezone.utc), all_day=True)],
            space=sudo_room(),
            now=NOW,
        )


# --------------------------------------------------------------------------- uids


def test_seventeen_occurrences_of_one_uid_produce_seventeen_uids() -> None:
    """**The regression this correction exists to prevent.**

    Every occurrence of a series carries the same source ``UID``. Namespacing
    on ``{space_id}:{source_uid}`` alone collapses all 17 into one event, and
    that shows up as a working feed with suspiciously few events rather than as
    an error.
    """
    parse = parse_ics_text(series_ics(17), space_id="sudo-room", label="events-manager", today=TODAY, now=NOW)
    occurrences = [event for event in parse.events if event.uid == "weekly-hack-night@sudoroom.test"]
    assert len(occurrences) == 17
    assert len({event.uid for event in occurrences}) == 1  # one source UID, as documented

    result = normalize_ics(parse, space=sudo_room(), now=NOW)
    series = [event for event in result if event.source_uid == "weekly-hack-night@sudoroom.test"]

    assert len(series) == 17
    assert len({event.uid for event in series}) == 17
    for event in series:
        assert event.recurring is True
        assert event.uid.startswith("sudo-room:weekly-hack-night@sudoroom.test:")
        # The occurrence start, and nothing else, is what separates them.
        assert event.uid.endswith(event.start_utc.strftime("%Y%m%dT%H%M%SZ"))


def test_a_non_recurring_event_keeps_the_two_part_uid() -> None:
    """Stability: an event that needs no occurrence key must not carry one."""
    parse = parse_ics_text(series_ics(17), space_id="sudo-room", today=TODAY, now=NOW)
    result = normalize_ics(parse, space=sudo_room(), now=NOW)
    one_off = [e for e in result if e.source_uid == "one-off-soldering@sudoroom.test"]

    assert len(one_off) == 1
    assert one_off[0].recurring is False
    assert one_off[0].uid == "sudo-room:one-off-soldering@sudoroom.test"


def test_recurrence_id_alone_does_not_mark_an_event_recurring() -> None:
    """``RECURRENCE-ID`` is set on every expanded occurrence, including one-offs.

    The related trap from the same pass: it cannot be used to detect a series,
    which is why ``recurring`` is read off the unexpanded VEVENT instead.
    """
    parse = parse_ics_text(series_ics(17), space_id="sudo-room", today=TODAY, now=NOW)
    one_off = next(e for e in parse.events if e.uid == "one-off-soldering@sudoroom.test")
    assert one_off.recurrence_id is not None
    assert one_off.recurring is False
    assert from_ics_event(one_off).recurring is False


def test_the_same_event_across_two_runs_produces_the_same_uid() -> None:
    """UID stability, stated as the thing subscribers actually experience."""
    monday = normalize_ics(
        parse_ics_text(series_ics(17), space_id="sudo-room", today=TODAY, now=NOW),
        space=sudo_room(),
        now=NOW,
    )
    tuesday = normalize_ics(
        parse_ics_text(
            series_ics(17),
            space_id="sudo-room",
            today=dt.date(2026, 8, 6),
            now=NOW + dt.timedelta(days=1),
        ),
        space=sudo_room(),
        now=NOW + dt.timedelta(days=1),
    )

    monday_uids = {e.uid for e in monday}
    tuesday_uids = {e.uid for e in tuesday}
    # Tuesday's window has moved, so it is a subset — but nothing was renamed.
    assert tuesday_uids <= monday_uids
    assert len(tuesday_uids) >= 16


def test_uid_does_not_move_when_the_run_timestamp_does() -> None:
    event = raw()
    first = one(event)
    second = normalize_events(
        [event], space=sudo_room(), source_label="luma", now=NOW + dt.timedelta(days=14)
    )[0]
    assert first.uid == second.uid
    assert first.content_hash == second.content_hash
    assert first.first_seen != second.first_seen  # the timestamps move; the identity does not


def test_an_event_with_no_source_uid_gets_a_stable_sha1() -> None:
    event = raw(source_uid=None)
    first = one(event)
    second = one(event)

    assert first.uid == second.uid
    assert len(first.uid) == 16
    assert all(char in "0123456789abcdef" for char in first.uid)
    assert first.source_uid is None


def test_the_hashed_uid_is_namespaced_by_space() -> None:
    """``space_id`` is inside the hash, so two spaces never collide."""
    event = raw(source_uid=None)
    mine = one(event)
    theirs = one(event, space=noisebridge())
    assert mine.uid != theirs.uid


def test_per_source_uid_keys_are_accommodated_without_ics_assumptions() -> None:
    """The three per-source rules already established for later adapters.

    All three already carry the occurrence, so all three are non-recurring by
    this module's reckoning and keep the stable two-part form.
    """
    start = dt.datetime(2026, 9, 1, 19, 0, tzinfo=dt.timezone.utc)
    assert (
        make_uid("maker-nexus", source_uid="2026-09-01_884412", start_utc=start)
        == "maker-nexus:2026-09-01_884412"
    )
    assert (
        make_uid("the-crucible", source_uid="4471:1788393600", start_utc=start)
        == "the-crucible:4471:1788393600"
    )
    assert (
        make_uid("hacker-dojo", source_uid="event_309912345@meetup.com", start_utc=start)
        == "hacker-dojo:event_309912345@meetup.com"
    )


def test_a_recurring_uid_without_a_start_is_refused() -> None:
    with pytest.raises(NormalizeError):
        make_uid("sudo-room", source_uid="series@sudoroom.test", recurring=True)


def test_no_uid_material_contains_a_timestamp_or_model_output() -> None:
    """Two events differing only in run time and enrichment hash identically."""
    start = dt.datetime(2026, 9, 1, 19, 0, tzinfo=dt.timezone.utc)
    assert make_uid("sudo-room", start_utc=start, title="Hack Night") == make_uid(
        "sudo-room", start_utc=start, title="Hack Night"
    )


# --------------------------------------------------------------------------- titles


@pytest.mark.parametrize(
    "text",
    [
        "Hack Night",
        "hack night",
        "HACK  NIGHT",
        "  Hack, Night!  ",
        "Hack — Night",
        "Hack\tNight\n",
        "“Hack” Night",
    ],
)
def test_normalize_title_is_punctuation_case_and_whitespace_insensitive(text: str) -> None:
    assert normalize_title(text) == "hack night"


def test_normalize_title_handles_nothing_at_all() -> None:
    assert normalize_title(None) == ""
    assert normalize_title("   ") == ""
    assert normalize_title("!!!") == ""


def test_a_retitled_event_keeps_its_hashed_uid_when_only_punctuation_moved() -> None:
    """The point of normalizing inside the hash: cosmetic edits must not churn."""
    before = one(raw(source_uid=None, title="Hack Night"))
    after = one(raw(source_uid=None, title="  hack, night!  "))
    assert before.uid == after.uid


# --------------------------------------------------------------------------- address


def test_address_override_applies_to_a_blank_location() -> None:
    """Hacker Dojo's Meetup feed has no ``LOCATION`` field at all."""
    event = one(raw(location=None), space=hacker_dojo())
    assert event.address == "855 Maude Ave, Mountain View, CA 94043"
    assert event.address_source is AddressSource.OVERRIDE
    assert event.off_site is False
    assert event.location_name is None


def test_address_override_applies_to_a_bare_url_location() -> None:
    """15% of Frontier Tower's events set ``LOCATION`` to a bare Luma URL."""
    event = one(raw(location="https://lu.ma/xyz123"), space=hacker_dojo())
    assert event.address == "855 Maude Ave, Mountain View, CA 94043"
    assert event.address_source is AddressSource.OVERRIDE
    # The evidence survives even though it is not an address.
    assert event.location_name == "https://lu.ma/xyz123"


def test_address_override_replaces_the_stale_house_address() -> None:
    """Sudo Room ships the pre-2014 ``549 48th St`` on every event."""
    stale = "549 48th St, Oakland, CA 94609"
    result = normalize_events(
        [raw(source_uid=f"e{n}", location=stale, start=dt.datetime(2026, 9, n, 19, 0, tzinfo=dt.timezone.utc))
         for n in range(1, 6)],
        space=sudo_room(),
        source_label="events-manager",
        now=NOW,
    )
    assert result.event_count == 5
    for event in result:
        assert event.address == "Omni Commons, 4799 Shattuck Ave, Oakland, CA 94609"
        assert event.address_source is AddressSource.OVERRIDE
        assert event.location_name == stale  # never erased


def test_address_override_does_not_clobber_a_genuine_offsite_location() -> None:
    """Sudo Room's Luma feed carries real off-site events.

    18 events at the stale house address and one at The Box Shop in San
    Francisco. The house address is a strict majority and gets corrected; the
    minority venue is a statement about *that* event and is believed.
    """
    stale = "549 48th St, Oakland, CA 94609"
    box_shop = "The Box Shop, 951 Hudson Ave, San Francisco, CA 94124"
    events = [
        raw(
            source_uid=f"house-{n}",
            location=stale,
            start=dt.datetime(2026, 9, 1, 19, 0, tzinfo=dt.timezone.utc) + dt.timedelta(days=n),
        )
        for n in range(18)
    ]
    events.append(
        raw(
            source_uid="offsite-1",
            title="Welding at The Box Shop",
            location=box_shop,
            start=dt.datetime(2026, 9, 25, 18, 0, tzinfo=dt.timezone.utc),
        )
    )

    result = normalize_events(events, space=sudo_room(), source_label="luma", now=NOW)
    offsite = next(event for event in result if event.source_uid == "offsite-1")

    assert offsite.address == box_shop
    assert offsite.address_source is AddressSource.SOURCE
    assert offsite.off_site is True
    assert all(
        event.address == "Omni Commons, 4799 Shattuck Ave, Oakland, CA 94609"
        for event in result
        if event.source_uid != "offsite-1"
    )


def test_a_location_naming_the_space_is_overridden_however_it_is_worded() -> None:
    event = one(raw(location="Sudo Room @ Omni Commons"))
    assert event.address_source is AddressSource.OVERRIDE
    assert event.off_site is False


def test_no_override_keeps_the_source_location() -> None:
    event = one(raw(location="272 Capp St, San Francisco, CA 94110"), space=noisebridge())
    assert event.address == "272 Capp St, San Francisco, CA 94110"
    assert event.address_source is AddressSource.SOURCE
    assert event.off_site is False


def test_no_override_and_no_location_is_simply_empty() -> None:
    event = one(raw(location=None), space=noisebridge())
    assert event.address is None
    assert event.address_source is AddressSource.NONE


# --------------------------------------------------------------------------- window


def test_an_event_three_years_out_is_dropped() -> None:
    """Sudo Room's export is pre-expanded to 2058. Two years is the backstop."""
    result = normalize_events(
        [raw(start=dt.datetime(2029, 9, 1, 19, 0, tzinfo=dt.timezone.utc))],
        space=sudo_room(),
        now=NOW,
    )
    assert result.event_count == 0
    assert len(result.dropped) == 1
    assert result.dropped[0].reason is DropReason.TOO_FAR
    assert result.dropped_for(DropReason.TOO_FAR)


def test_an_event_just_inside_two_years_is_kept() -> None:
    event = one(raw(start=NOW + dt.timedelta(days=700)))
    assert event.start_utc == NOW + dt.timedelta(days=700)


def test_a_past_event_is_dropped() -> None:
    result = normalize_events(
        [raw(start=dt.datetime(2026, 7, 1, 19, 0, tzinfo=dt.timezone.utc))],
        space=sudo_room(),
        now=NOW,
    )
    assert result.event_count == 0
    assert result.dropped[0].reason is DropReason.PAST


def test_an_event_earlier_today_is_kept() -> None:
    """Measured against the start of today locally, not against the wall clock.

    A midday re-run must not delete this morning's events from the calendar.
    """
    noon = dt.datetime(2026, 8, 5, 20, 0, tzinfo=dt.timezone.utc)  # 13:00 Pacific
    this_morning = dt.datetime(2026, 8, 5, 17, 0, tzinfo=dt.timezone.utc)  # 10:00 Pacific
    result = normalize_events([raw(start=this_morning)], space=sudo_room(), now=noon)
    assert result.event_count == 1


def test_an_all_day_event_survives_its_own_last_day() -> None:
    result = normalize_events(
        [
            raw(
                start=dt.date(2026, 8, 5),
                end=dt.date(2026, 8, 5),
                all_day=True,
                dtstart_form="date",
                source_tz=None,
            )
        ],
        space=sudo_room(),
        now=dt.datetime(2026, 8, 5, 23, 0, tzinfo=dt.timezone.utc),
    )
    assert result.event_count == 1


def test_an_event_with_no_start_is_dropped_and_reported() -> None:
    result = normalize_events([raw(start=None)], space=sudo_room(), now=NOW)
    assert result.event_count == 0
    assert result.dropped[0].reason is DropReason.NO_START


# --------------------------------------------------------------------------- quarantine


def test_a_250_character_title_is_quarantined_not_dropped() -> None:
    """The classic LLM-extraction signature: the description ate the title."""
    long_title = "Intro to Welding " * 15
    assert len(long_title) > 250 - 40
    long_title = long_title[:250]

    result = normalize_events([raw(title=long_title)], space=sudo_room(), now=NOW)

    assert result.event_count == 0
    assert len(result.dropped) == 0  # not dropped — withheld, and inspectable
    assert len(result.quarantined) == 1
    held = result.quarantined[0]
    assert held.quarantine is QuarantineReason.TITLE_TOO_LONG
    assert held.is_quarantined is True
    assert held.title == long_title  # kept verbatim so the failure can be read
    assert result.quarantined_for(QuarantineReason.TITLE_TOO_LONG)


def test_a_title_of_exactly_the_limit_is_published() -> None:
    result = normalize_events([raw(title="x" * MAX_TITLE_CHARS)], space=sudo_room(), now=NOW)
    assert result.event_count == 1


def test_an_empty_title_is_quarantined() -> None:
    result = normalize_events([raw(title="   ")], space=sudo_room(), now=NOW)
    assert result.event_count == 0
    assert result.quarantined[0].quarantine is QuarantineReason.EMPTY_TITLE


def test_nothing_leaves_this_module_without_landing_somewhere() -> None:
    """Kept, quarantined or dropped — the three collections must total the input."""
    events = [
        raw(source_uid="ok"),
        raw(source_uid="held", title=""),
        raw(source_uid="past", start=dt.datetime(2026, 1, 1, 19, 0, tzinfo=dt.timezone.utc)),
        raw(source_uid="far", start=dt.datetime(2030, 1, 1, 19, 0, tzinfo=dt.timezone.utc)),
    ]
    result = normalize_events(events, space=sudo_room(), now=NOW)
    assert result.input_count == 4
    assert (result.event_count, len(result.quarantined), len(result.dropped)) == (1, 1, 2)


# --------------------------------------------------------------------------- record


def test_the_canonical_record_carries_the_handoff_schema() -> None:
    """Part 3's schema, field for field, plus the provenance later stages need."""
    event = one(
        raw(
            title="Hack Night",
            location="549 48th St, Oakland, CA 94609",
            description="Open to all.",
            url="https://sudoroom.org/events/hack-night",
            categories=("workshop-events",),
        )
    )
    record = event.as_dict()

    for key in (
        "uid",
        "space_id",
        "source_label",
        "title",
        "start_utc",
        "end_utc",
        "tz",
        "all_day",
        "location_name",
        "address",
        "url",
        "price",
        "description",
        "categories",
        "summary_line",
        "rrule",
        "first_seen",
        "last_seen",
        "content_hash",
    ):
        assert key in record, key

    assert record["start_utc"] == "2026-09-01T19:00:00+00:00"
    assert record["tz"] == "UTC"
    assert record["categories"] == ["workshop-events"]
    # Model-assigned fields are empty here: enrich fills them, and nothing a
    # model wrote may ever reach the UID.
    assert record["summary_line"] is None


def test_source_categories_survive_for_the_filter_stage() -> None:
    """Issue 0010's ``categories_exclude`` runs on these."""
    event = one(raw(categories=("noisebridge", "events happening elsewhere")))
    assert event.categories == ("noisebridge", "events happening elsewhere")


def test_content_hash_tracks_content_and_not_the_run() -> None:
    start = dt.datetime(2026, 9, 1, 19, 0, tzinfo=dt.timezone.utc)
    base = dict(
        title="Hack Night",
        start_utc=start,
        end_utc=None,
        location_name=None,
        address=None,
        url=None,
        description=None,
    )
    assert content_hash(**base) == content_hash(**base)
    assert content_hash(**{**base, "title": "Hack Night!"}) != content_hash(**base)
    assert content_hash(**{**base, "address": "Omni Commons"}) != content_hash(**base)


def test_events_come_back_sorted_by_start() -> None:
    result = normalize_events(
        [
            raw(source_uid="b", start=dt.datetime(2026, 9, 3, 19, 0, tzinfo=dt.timezone.utc)),
            raw(source_uid="a", start=dt.datetime(2026, 9, 1, 19, 0, tzinfo=dt.timezone.utc)),
        ],
        space=sudo_room(),
        now=NOW,
    )
    assert [event.source_uid for event in result] == ["a", "b"]


# --------------------------------------------------------------------------- ics seam


def test_a_failed_parse_normalizes_to_empty_rather_than_raising() -> None:
    """A 200 carrying ``text/html`` is the adapter's verdict to report."""
    parse = parse_ics_text(load("homepage.html"), space_id="sudo-room", today=TODAY, now=NOW)
    assert not parse.ok
    result = normalize_ics(parse, space=sudo_room(), now=NOW)
    assert result.event_count == 0
    assert result.dropped == ()


def test_mixed_dtstart_forms_in_one_feed_all_land_in_utc() -> None:
    """Sequoia Fabrica genuinely mixes bare-UTC ``Z`` and ``TZID=`` in one file."""
    parse = parse_ics_text(
        load("sequoia-mixed-dtstart.ics"), space_id="sequoia-fabrica", today=TODAY, now=NOW
    )
    result = normalize_ics(parse, space=noisebridge(), source_label="gcal", now=NOW)

    assert result.event_count > 0
    for event in result:
        assert event.start_utc.tzinfo is not None
        assert event.start_utc.utcoffset() == dt.timedelta(0)
        assert event.tz


def test_the_sudo_room_export_normalizes_without_a_naive_datetime() -> None:
    """The 8 MB pre-expanded feed, in miniature: every event must survive intact."""
    parse = parse_ics_text(
        load("sudoroom-preexpanded.ics"), space_id="sudo-room", label="events-manager",
        today=TODAY, now=NOW,
    )
    result = normalize_ics(parse, space=sudo_room(), now=NOW)

    assert result.event_count > 0
    for event in result:
        assert event.start_utc.tzinfo is not None
        assert event.uid.startswith("sudo-room:")
        assert event.space_id == "sudo-room"
        assert event.source_label == "events-manager"
