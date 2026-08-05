"""Tests for the merged ICS emit (issue 0011).

**No test here touches the network**, and none of them trusts the builder about
what it built: every assertion about the output is made after parsing the bytes
back through ``icalendar``, because the whole point of the round-trip gate is
that "we meant to write X" and "a client reading this file sees X" are
different claims.

What is being defended:

**Expanded instances, never ``RRULE``.** The record still carries ``rrule`` and
``recurring`` from the adapter; emitting the rule alongside the already-expanded
occurrences would duplicate a whole series in every client that honors it. The
absence is asserted on parsed output, not on the source string.

**UID verbatim.** It is the subscriber-stability contract. If emit transformed
it — prefixed it, hashed it, lowercased it — every subscriber would see every
event as new the night the transform changed.

**All-day is a different shape.** ``VALUE=DATE`` with RFC 5545's *exclusive*
``DTEND``, written from the local dates issue 0009 preserved, not from the UTC
instants. A three-day festival must come back as three days.

**Nothing half-written reaches ``out/``.** A corrupt calendar is rejected before
anything moves, and an interrupted write leaves the previous file in place with
no temp files behind it. A stale ``.ics`` is recoverable; a truncated one that a
hundred clients have cached is not.

**Escaping is ``icalendar``'s job, and it is tested anyway.** A space name with
a comma in it is likely (``Ace Makerspace, Inc``), and a source title with an
embedded newline is common enough in the surveyed feeds to be worth a test.
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import replace
from pathlib import Path

import pytest
from icalendar import Calendar

from pipeline.config import Space
from pipeline.normalize import Event, QuarantineReason
from pipeline.emit_ics import (
    CALNAME,
    DESCRIPTION_LIMIT,
    ELLIPSIS,
    MERGED_NAME,
    PRODID,
    EmitResult,
    InvalidCalendarError,
    UnknownSpaceError,
    build_calendar,
    build_description,
    build_summary,
    emit_ics,
    emit_string,
    publishable,
    render,
    truncate,
    validate_ics,
    write_atomic,
)

#: The source-survey date, at the hour launchd runs the job.
NOW = dt.datetime(2026, 8, 5, 3, 15, tzinfo=dt.timezone.utc)
START = dt.datetime(2026, 9, 1, 19, 0, tzinfo=dt.timezone.utc)

PACIFIC = "America/Los_Angeles"


# --------------------------------------------------------------------------- helpers


def ace(**kwargs: object) -> Space:
    """Ace Makerspace, with a comma in the name on purpose."""
    fields: dict[str, object] = {
        "id": "ace-makerspace",
        "name": "Ace Makerspace, Inc",
        "city": "Oakland",
        "region": "east-bay",
        "url": "https://acemakerspace.org/",
    }
    fields.update(kwargs)
    return Space(**fields)  # type: ignore[arg-type]


def noisebridge() -> Space:
    return Space(
        id="noisebridge",
        name="Noisebridge",
        city="San Francisco",
        region="san-francisco",
        url="https://noisebridge.net/",
    )


SPACES = [ace(), noisebridge()]


def event(**kwargs: object) -> Event:
    """A timed, aware, publishable :class:`Event` with fields overridden."""
    fields: dict[str, object] = {
        "uid": "ace-makerspace:evt-1@acemakerspace.org",
        "space_id": "ace-makerspace",
        "source_label": "tribe-events",
        "title": "Sewing 101 Bootcamp",
        "start_utc": START,
        "end_utc": START + dt.timedelta(hours=2),
        "tz": PACIFIC,
        "first_seen": NOW,
        "last_seen": NOW,
    }
    fields.update(kwargs)
    return Event(**fields)  # type: ignore[arg-type]


def all_day_event(**kwargs: object) -> Event:
    """A three-day all-day festival: 4-6 September, inclusive."""
    fields: dict[str, object] = {
        "uid": "ace-makerspace:fest-2026",
        "space_id": "ace-makerspace",
        "source_label": "tribe-events",
        "title": "Maker Fest",
        "all_day": True,
        "start_date": dt.date(2026, 9, 4),
        "end_date": dt.date(2026, 9, 6),
        # The instants issue 0009 anchors: midnight Pacific, exclusive end.
        "start_utc": dt.datetime(2026, 9, 4, 7, 0, tzinfo=dt.timezone.utc),
        "end_utc": dt.datetime(2026, 9, 7, 7, 0, tzinfo=dt.timezone.utc),
        "tz": PACIFIC,
        "first_seen": NOW,
        "last_seen": NOW,
    }
    fields.update(kwargs)
    return Event(**fields)  # type: ignore[arg-type]


def parse(data: bytes) -> list:
    """Round-trip *data* and hand back its ``VEVENT``s."""
    return list(Calendar.from_ical(data).walk("VEVENT"))


def text(component, name: str) -> str:
    """One text property, unescaped — ``str(vText)`` is the decoded form."""
    return str(component.get(name))


# --------------------------------------------------------------------------- calendar


def test_calendar_headers_are_the_handoff_values_verbatim() -> None:
    data = emit_string([event()], SPACES, dtstamp=NOW)
    calendar = Calendar.from_ical(data)

    assert str(calendar.get("PRODID")) == PRODID
    assert str(calendar.get("VERSION")) == "2.0"
    assert str(calendar.get("X-WR-CALNAME")) == CALNAME == "Bay Area Makerspaces"
    assert str(calendar.get("X-WR-TIMEZONE")) == "America/Los_Angeles"


def test_round_trip_yields_the_same_event_count() -> None:
    events = [
        event(uid="ace-makerspace:a", start_utc=START),
        event(uid="ace-makerspace:b", start_utc=START + dt.timedelta(days=1)),
        event(
            uid="noisebridge:c",
            space_id="noisebridge",
            start_utc=START + dt.timedelta(days=2),
        ),
        all_day_event(),
    ]
    data = emit_string(events, SPACES, dtstamp=NOW)

    assert len(parse(data)) == len(events)
    assert validate_ics(data, expected_count=len(events)).event_count == 4


def test_events_are_ordered_by_start_and_output_is_deterministic() -> None:
    late = event(uid="ace-makerspace:late", start_utc=START + dt.timedelta(days=3))
    early = event(uid="ace-makerspace:early", start_utc=START)

    first = emit_string([late, early], SPACES, dtstamp=NOW)
    second = emit_string([early, late], SPACES, dtstamp=NOW)

    assert first == second, "an unchanged event set must serialize byte-identically"
    assert [str(v.get("UID")) for v in parse(first)] == [
        "ace-makerspace:early",
        "ace-makerspace:late",
    ]


# --------------------------------------------------------------------------- vevent


def test_summary_carries_the_space_prefix() -> None:
    data = emit_string([event(title="Sewing 101 Bootcamp")], SPACES, dtstamp=NOW)
    (vevent,) = parse(data)

    assert text(vevent, "SUMMARY") == "[Ace Makerspace, Inc] Sewing 101 Bootcamp"
    assert build_summary(event(), ace()) == "[Ace Makerspace, Inc] Sewing 101 Bootcamp"


def test_uid_is_preserved_verbatim() -> None:
    uid = "ace-makerspace:Evt-99@AceMakerspace.org:20260901T190000Z"
    data = emit_string([event(uid=uid)], SPACES, dtstamp=NOW)
    (vevent,) = parse(data)

    assert str(vevent.get("UID")) == uid, (
        "the UID is the subscriber-stability contract; transforming it makes "
        "every event look new"
    )


def test_every_event_carries_url_and_organizer_naming_the_space() -> None:
    with_own_url = event(uid="ace-makerspace:own", url="https://acemakerspace.org/e/1")
    without = event(uid="ace-makerspace:bare", url=None)
    data = emit_string([with_own_url, without], SPACES, dtstamp=NOW)

    urls = {str(v.get("UID")): str(v.get("URL")) for v in parse(data)}
    assert urls["ace-makerspace:own"] == "https://acemakerspace.org/e/1"
    assert urls["ace-makerspace:bare"] == "https://acemakerspace.org/"

    for vevent in parse(data):
        organizer = vevent.get("ORGANIZER")
        assert str(organizer) == "https://acemakerspace.org/"
        assert organizer.params["CN"] == "Ace Makerspace, Inc"


def test_prefer_event_url_off_points_every_event_at_the_space_page() -> None:
    data = emit_string(
        [event(url="https://luma.com/event/evt-abc")],
        SPACES,
        dtstamp=NOW,
        prefer_event_url=False,
    )
    (vevent,) = parse(data)
    assert str(vevent.get("URL")) == "https://acemakerspace.org/"


def test_location_and_categories_survive() -> None:
    data = emit_string(
        [
            event(
                location_name="Ace Makerspace, 6050 Lowell St, Oakland",
                address="Ace Makerspace, 6050 Lowell St, Oakland",
                categories=("Textiles", "Woodshop"),
            )
        ],
        SPACES,
        dtstamp=NOW,
    )
    (vevent,) = parse(data)

    assert text(vevent, "LOCATION") == "Ace Makerspace, 6050 Lowell St, Oakland"
    assert [str(c) for c in vevent.get("CATEGORIES").cats] == ["Textiles", "Woodshop"]


def test_dtstamp_is_the_run_timestamp_on_every_event() -> None:
    data = emit_string([event(), all_day_event()], SPACES, dtstamp=NOW)
    for vevent in parse(data):
        assert vevent.get("DTSTAMP").dt == NOW


# --------------------------------------------------------------------------- description


def test_long_description_is_truncated_on_a_word_boundary_and_ends_with_the_link() -> None:
    body = (
        "Learn to thread, tension and troubleshoot a domestic sewing machine "
        "in this hands-on evening bootcamp for absolute beginners. "
    ) * 6
    source = event(description=body, url="https://acemakerspace.org/e/sewing")

    data = emit_string([source], SPACES, dtstamp=NOW)
    (vevent,) = parse(data)
    description = text(vevent, "DESCRIPTION")

    assert description.endswith("https://acemakerspace.org/e/sewing")

    head = description.split("\n\n")[0]
    assert head.endswith(ELLIPSIS), "a cut description must say it was cut"
    assert len(head) <= DESCRIPTION_LIMIT + len(ELLIPSIS)

    # The defended property: the last word is a whole word of the source.
    kept = head[: -len(ELLIPSIS)].split()
    assert body.split()[: len(kept)] == kept, "truncation must not cut mid-word"
    assert len(kept) > 10, "the cut should keep most of a 300-character budget"


def test_short_description_is_not_truncated_but_still_gets_the_link() -> None:
    data = emit_string(
        [event(description="Bring your own fabric.", url="https://acemakerspace.org/e/2")],
        SPACES,
        dtstamp=NOW,
    )
    (vevent,) = parse(data)
    description = text(vevent, "DESCRIPTION")

    assert description.startswith("Bring your own fabric.")
    assert ELLIPSIS not in description
    assert description.endswith("https://acemakerspace.org/e/2")


def test_missing_description_still_links_back() -> None:
    data = emit_string([event(description=None)], SPACES, dtstamp=NOW)
    (vevent,) = parse(data)

    assert text(vevent, "DESCRIPTION") == (
        "Ace Makerspace, Inc — https://acemakerspace.org/"
    )


def test_price_is_carried_as_source_text() -> None:
    described = build_description(event(price="$45 / $30 members"), ace())
    assert "Price: $45 / $30 members" in described
    assert described.endswith("https://acemakerspace.org/")


@pytest.mark.parametrize(
    "source, expected",
    [
        (None, ""),
        ("", ""),
        ("short", "short"),
        ("  padded  ", "padded"),
        ("one two three four", "one two" + ELLIPSIS),
        ("supercalifragilistic", "supercalif" + ELLIPSIS),
    ],
)
def test_truncate_cuts_on_whitespace(source: str | None, expected: str) -> None:
    assert truncate(source, 10) == expected


# --------------------------------------------------------------------------- shapes


def test_all_day_event_emits_value_date_with_an_exclusive_dtend() -> None:
    data = emit_string([all_day_event()], SPACES, dtstamp=NOW)
    (vevent,) = parse(data)

    start = vevent.get("DTSTART")
    end = vevent.get("DTEND")

    assert start.params.get("VALUE") == "DATE"
    assert end.params.get("VALUE") == "DATE"
    assert start.dt == dt.date(2026, 9, 4)
    assert not isinstance(start.dt, dt.datetime), "all-day must not become an instant"
    # Inclusive last day is the 6th; RFC 5545's DTEND is exclusive.
    assert end.dt == dt.date(2026, 9, 7)
    assert (end.dt - start.dt).days == 3

    assert b"DTSTART;VALUE=DATE:20260904" in data
    assert b"DTEND;VALUE=DATE:20260907" in data


def test_all_day_uses_the_local_dates_not_the_utc_instants() -> None:
    """The instant is 07:00Z; rendering from it would print the wrong day east of here."""
    data = emit_string([all_day_event()], SPACES, dtstamp=NOW)
    assert b"20260904T070000Z" not in data


def test_timed_event_emits_utc() -> None:
    data = emit_string([event()], SPACES, dtstamp=NOW)
    (vevent,) = parse(data)

    assert vevent.get("DTSTART").dt == START
    assert vevent.get("DTSTART").dt.utcoffset() == dt.timedelta(0)
    assert vevent.get("DTEND").dt == START + dt.timedelta(hours=2)
    assert b"DTSTART:20260901T190000Z" in data
    assert b"TZID" not in data


def test_timed_event_with_no_end_omits_dtend() -> None:
    data = emit_string([event(end_utc=None)], SPACES, dtstamp=NOW)
    (vevent,) = parse(data)
    assert vevent.get("DTEND") is None


def test_no_rrule_is_emitted_even_for_a_recurring_source_event() -> None:
    """The Sudo Room case: one series, 17 occurrences, one source UID."""
    occurrences = [
        event(
            uid=f"sudo-room:series@sudoroom.org:{index}",
            space_id="noisebridge",
            recurring=True,
            rrule="FREQ=WEEKLY;BYDAY=TU",
            start_utc=START + dt.timedelta(days=7 * index),
        )
        for index in range(3)
    ]
    data = emit_string(occurrences, SPACES, dtstamp=NOW)

    assert b"RRULE" not in data
    assert b"RDATE" not in data
    for vevent in parse(data):
        assert vevent.get("RRULE") is None
    assert len(parse(data)) == 3, "the expansion is what is published, not the rule"


# --------------------------------------------------------------------------- escaping


def test_comma_semicolon_backslash_and_newline_survive_a_round_trip() -> None:
    title = "Sewing 101, Level 2; bring fabric\nand a \\ spool"
    data = emit_string(
        [event(title=title, description="Notes: a, b; c\\d\nnew line")],
        SPACES,
        dtstamp=NOW,
    )

    # Escaped on the wire...
    assert rb"SUMMARY:[Ace Makerspace\, Inc] Sewing 101\, Level 2\; bring" in data
    # ...and identical coming back.
    (vevent,) = parse(data)
    assert text(vevent, "SUMMARY") == f"[Ace Makerspace, Inc] {title}"
    assert text(vevent, "DESCRIPTION").startswith("Notes: a, b; c\\d\nnew line")


def test_long_lines_are_folded_and_unfold_to_the_original() -> None:
    long_title = "Laser " * 40
    data = emit_string([event(title=long_title)], SPACES, dtstamp=NOW)

    lines = data.split(b"\r\n")
    assert all(len(line) <= 75 for line in lines), "RFC 5545 folding at 75 octets"
    assert any(line.startswith(b" ") for line in lines), "expected a continuation line"

    (vevent,) = parse(data)
    assert text(vevent, "SUMMARY") == f"[Ace Makerspace, Inc] {long_title}"


# --------------------------------------------------------------------------- quarantine


def test_quarantined_events_are_never_published_and_are_counted(tmp_path: Path) -> None:
    held = replace(
        event(uid="ace-makerspace:held", title="x" * 300),
        quarantine=QuarantineReason.TITLE_TOO_LONG,
    )

    kept, skipped = publishable([event(), held])
    assert len(kept) == 1 and skipped == 1

    result = emit_ics([event(), held], spaces=SPACES, out_dir=tmp_path, dtstamp=NOW)
    assert result.event_count == 1
    assert result.skipped_quarantined == 1
    assert len(parse(result.merged_path.read_bytes())) == 1


def test_an_event_naming_an_unknown_space_fails_loudly(tmp_path: Path) -> None:
    stray = event(uid="ghost:1", space_id="ghost-space")
    with pytest.raises(UnknownSpaceError, match="ghost-space"):
        emit_ics([stray], spaces=SPACES, out_dir=tmp_path, dtstamp=NOW)
    assert not (tmp_path / MERGED_NAME).exists()


# --------------------------------------------------------------------------- files


def test_emit_writes_merged_and_per_space_files(tmp_path: Path) -> None:
    events = [
        event(uid="ace-makerspace:a"),
        event(uid="ace-makerspace:b", start_utc=START + dt.timedelta(days=1)),
        all_day_event(),
        event(
            uid="noisebridge:c",
            space_id="noisebridge",
            source_label="gcal",
            start_utc=START + dt.timedelta(days=2),
        ),
    ]

    result = emit_ics(events, spaces=SPACES, out_dir=tmp_path, dtstamp=NOW)

    assert isinstance(result, EmitResult)
    assert result.merged_path == tmp_path / MERGED_NAME
    assert result.event_count == 4
    assert result.counts_by_space == {"ace-makerspace": 3, "noisebridge": 1}
    assert set(result.space_paths) == {"ace-makerspace", "noisebridge"}
    assert result.summary()["by_space"] == {"ace-makerspace": 3, "noisebridge": 1}

    for path in result.paths:
        assert path.exists()
    assert (tmp_path / "spaces" / "ace-makerspace.ics").exists()


def test_per_space_files_hold_only_that_space_and_sum_to_the_merged_total(
    tmp_path: Path,
) -> None:
    events = [
        event(uid="ace-makerspace:a"),
        event(uid="ace-makerspace:b", start_utc=START + dt.timedelta(days=1)),
        all_day_event(),
        event(
            uid="noisebridge:c",
            space_id="noisebridge",
            start_utc=START + dt.timedelta(days=2),
        ),
        event(
            uid="noisebridge:d",
            space_id="noisebridge",
            start_utc=START + dt.timedelta(days=3),
        ),
    ]
    result = emit_ics(events, spaces=SPACES, out_dir=tmp_path, dtstamp=NOW)

    merged_uids = {str(v.get("UID")) for v in parse(result.merged_path.read_bytes())}
    total = 0
    for space_id, path in result.space_paths.items():
        vevents = parse(path.read_bytes())
        total += len(vevents)
        assert {str(v.get("X-MAKER-SPACE-ID")) for v in vevents} == {space_id}
        assert {str(v.get("UID")) for v in vevents} <= merged_uids
        calendar = Calendar.from_ical(path.read_bytes())
        assert str(calendar.get("PRODID")) == PRODID
        assert space_id in {"ace-makerspace", "noisebridge"}

    assert total == len(merged_uids) == result.event_count == 5


def test_per_space_can_be_switched_off(tmp_path: Path) -> None:
    result = emit_ics(
        [event()], spaces=SPACES, out_dir=tmp_path, dtstamp=NOW, per_space=False
    )
    assert result.space_paths == {}
    assert not (tmp_path / "spaces").exists()


def test_emit_replaces_an_existing_file_in_place(tmp_path: Path) -> None:
    target = tmp_path / MERGED_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"stale")

    emit_ics([event()], spaces=SPACES, out_dir=tmp_path, dtstamp=NOW)
    assert target.read_bytes().startswith(b"BEGIN:VCALENDAR")


# --------------------------------------------------------------------------- validation


def test_validation_rejects_a_corrupted_calendar() -> None:
    good = emit_string([event()], SPACES, dtstamp=NOW)
    truncated = good[: len(good) // 2]

    with pytest.raises(InvalidCalendarError, match="does not parse"):
        validate_ics(truncated, expected_count=1)

    with pytest.raises(InvalidCalendarError, match="does not parse"):
        validate_ics(b"<html>200 OK but not a calendar</html>", expected_count=0)


def test_validation_rejects_a_count_that_does_not_match() -> None:
    data = emit_string([event()], SPACES, dtstamp=NOW)
    with pytest.raises(InvalidCalendarError, match="expected 5"):
        validate_ics(data, expected_count=5)
    # ...and accepts it when the tolerance is stated.
    assert validate_ics(data, expected_count=2, tolerance=1).event_count == 1


def test_validation_rejects_a_missing_required_property() -> None:
    data = emit_string([event()], SPACES, dtstamp=NOW)
    stripped = b"\r\n".join(
        line for line in data.split(b"\r\n") if not line.startswith(b"URL:")
    )
    with pytest.raises(InvalidCalendarError, match="has no URL"):
        validate_ics(stripped, expected_count=1)


def test_validation_rejects_a_smuggled_rrule() -> None:
    data = emit_string([event()], SPACES, dtstamp=NOW)
    smuggled = data.replace(b"UID:", b"RRULE:FREQ=WEEKLY\r\nUID:", 1)
    with pytest.raises(InvalidCalendarError, match="RRULE"):
        validate_ics(smuggled, expected_count=1)


def test_validation_rejects_duplicate_uids() -> None:
    events = [event(uid="ace-makerspace:same"), event(uid="ace-makerspace:same",
                                                      start_utc=START + dt.timedelta(days=1))]
    data = render(build_calendar(events, SPACES, dtstamp=NOW))
    with pytest.raises(InvalidCalendarError, match="duplicate UIDs"):
        validate_ics(data, expected_count=2)


def test_validation_rejects_a_naive_dtstart() -> None:
    data = emit_string([event()], SPACES, dtstamp=NOW)
    naive = data.replace(b"DTSTART:20260901T190000Z", b"DTSTART:20260901T190000")
    with pytest.raises(InvalidCalendarError, match="naive DTSTART"):
        validate_ics(naive, expected_count=1)


def test_validation_rejects_an_all_day_dtend_that_is_not_exclusive() -> None:
    data = emit_string([all_day_event()], SPACES, dtstamp=NOW)
    collapsed = data.replace(b"DTEND;VALUE=DATE:20260907", b"DTEND;VALUE=DATE:20260904")
    with pytest.raises(InvalidCalendarError, match="exclusive"):
        validate_ics(collapsed, expected_count=1)


def test_validation_reports_what_it_found() -> None:
    data = emit_string([event(), all_day_event()], SPACES, dtstamp=NOW)
    report = validate_ics(data, expected_count=2, label="out/calendar.ics")

    assert report.label == "out/calendar.ics"
    assert report.event_count == 2
    assert report.unique_uids == 2
    assert report.all_day_count == 1
    assert report.space_ids == frozenset({"ace-makerspace"})


def test_a_corrupt_render_is_never_published(tmp_path: Path, monkeypatch) -> None:
    """Validation runs before anything moves: the previous file must survive."""
    target = tmp_path / MERGED_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"BEGIN:VCALENDAR\r\nPREVIOUS RUN\r\nEND:VCALENDAR\r\n")

    monkeypatch.setattr("pipeline.emit_ics.render", lambda calendar: b"garbage")

    with pytest.raises(InvalidCalendarError):
        emit_ics([event()], spaces=SPACES, out_dir=tmp_path, dtstamp=NOW)

    assert target.read_bytes() == b"BEGIN:VCALENDAR\r\nPREVIOUS RUN\r\nEND:VCALENDAR\r\n"
    assert list(tmp_path.glob("*.tmp")) == []


def test_a_per_space_failure_leaves_the_merged_file_alone(
    tmp_path: Path, monkeypatch
) -> None:
    """Every file is validated before any file is written."""
    merged = tmp_path / MERGED_NAME
    merged.parent.mkdir(parents=True, exist_ok=True)
    merged.write_bytes(b"previous")

    calls: list[int] = []

    real = validate_ics

    def flaky(data, **kwargs):
        calls.append(1)
        if len(calls) > 1:  # the first per-space calendar
            raise InvalidCalendarError("boom")
        return real(data, **kwargs)

    monkeypatch.setattr("pipeline.emit_ics.validate_ics", flaky)

    with pytest.raises(InvalidCalendarError):
        emit_ics([event()], spaces=SPACES, out_dir=tmp_path, dtstamp=NOW)

    assert merged.read_bytes() == b"previous"
    assert not (tmp_path / "spaces" / "ace-makerspace.ics").exists()


# --------------------------------------------------------------------------- atomic


def test_write_atomic_replaces_the_file(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "calendar.ics"
    write_atomic(target, b"first")
    write_atomic(target, b"second")

    assert target.read_bytes() == b"second"
    assert [p.name for p in tmp_path.rglob("*") if p.is_file()] == ["calendar.ics"]


def test_a_failed_write_leaves_no_partial_file(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "calendar.ics"
    target.write_bytes(b"previous")

    def boom(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)

    with pytest.raises(OSError, match="disk full"):
        write_atomic(target, b"a" * 4096)

    assert target.read_bytes() == b"previous", "the live file must survive"
    assert list(tmp_path.iterdir()) == [target], "no temp file may be left behind"


def test_a_failed_write_creates_nothing_when_there_was_nothing(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "calendar.ics"

    def boom(src: object, dst: object) -> None:
        raise OSError("interrupted")

    monkeypatch.setattr(os, "replace", boom)

    with pytest.raises(OSError):
        write_atomic(target, b"partial")

    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_emit_of_an_empty_event_set_writes_an_empty_but_valid_calendar(
    tmp_path: Path,
) -> None:
    """A space with no upcoming events is a documented state, not a failure."""
    result = emit_ics([], spaces=SPACES, out_dir=tmp_path, dtstamp=NOW)

    assert result.event_count == 0
    assert result.space_paths == {}
    data = result.merged_path.read_bytes()
    assert parse(data) == []
    assert validate_ics(data, expected_count=0).event_count == 0
