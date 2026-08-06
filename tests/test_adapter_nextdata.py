"""Tests for the ``nextdata`` adapter (issue 0023).

**No test here touches the network.** Issue 0001 — the bot about page that
``$MAKER_CALENDAR_CONTACT`` points at — is still open, so a live request would
go out under a User-Agent naming a page that does not resolve. Every request in
this file goes through ``httpx.MockTransport`` and every payload is a
hand-authored fixture under ``tests/fixtures/nextdata/``. That matters more than
usual here: Eventbrite's terms restrict scraping, and a test suite that fetched
their organizer pages on every run would be the exact behavior those terms are
aimed at.

The fixtures are **representative** organizer pages, not copies of the live 151
KB shells. The Crucible's seven "Free Crucible Tour" entries are the ones the
2026-08-05 survey recorded; four more exercise what that survey could not.

Seven things are being defended.

**A declared zero is an observation, not a failure.** Three of the four
registered consumers report ``upcomingEventsTotal: 0``. The adapter says so with
``ok``, ``parsed`` and ``reported_zero`` all true and no events — and a page that
merely *looks* empty (renamed key, missing total, contradictory count) is a
reported problem instead. That distinction is the whole issue.

**A start is three fields.** ``start_date`` + ``start_time`` + ``timezone`` are
assembled, and the timezone is the event's own: the New York panel in the
fixture converts at ``-04:00`` while the Oakland tours convert at ``-07:00``
before 2026-11-01 and ``-08:00`` after it.

**``primary_venue.address`` is a real location** — the only one in the whole
registry for The Crucible — in whichever of Eventbrite's four address forms it
arrives.

**A missing blob is reported, and so is a renamed key.** Both would otherwise be
an empty calendar, which is the worst outcome this project has.

**Not one ``application/ld+json`` block is on these pages.** That is why this
adapter exists: Humanmade's source was registered as ``jsonld`` and would have
returned empty forever without erroring.

**UIDs are ``{id}:{start_epoch}``** — eight events sharing the title "Free
Crucible Tour" have eight distinct, run-stable UIDs.

**It is built on ``embedded_json``**, as issue 0021 designed for: the field map
is that adapter's :class:`FieldMap` with a dotted ``items`` path, and the item
walk, UID rule and horizon clip are shared code rather than a second parser.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from pipeline.adapters.embedded_json import DEFAULT_ZONE, FieldMap, json_script_ids, json_scripts
from pipeline.adapters.jsonld import ld_blocks
from pipeline.adapters.nextdata import (
    EVENTBRITE_ORGANIZER_FIELD_MAP,
    EVENTS_PATH,
    NEXT_DATA_SCRIPT_ID,
    NextDataParse,
    NextDataProblem,
    assemble_start,
    event_zone,
    fetch_nextdata,
    parse,
    parse_nextdata,
    parse_nextdata_text,
    venue_address,
)
from pipeline.cli import ADAPTERS, implemented_adapters, is_runnable, process_source
from pipeline.config import Registry, SourceRef, load_registry
from pipeline.fetch import FetchResult, Fetcher, Outcome
from pipeline.normalize import normalize_ics

FIXTURES = Path(__file__).parent / "fixtures" / "nextdata"

#: The source-survey date. Every fixture is authored relative to it.
TODAY = dt.date(2026, 8, 5)
NOW = dt.datetime(2026, 8, 5, 3, 15, tzinfo=dt.timezone.utc)
HORIZON_END = dt.date(2026, 12, 3)  # TODAY + 120 days, inclusive

PDT = dt.timezone(dt.timedelta(hours=-7))
PST = dt.timezone(dt.timedelta(hours=-8))
EDT = dt.timezone(dt.timedelta(hours=-4))

TEST_CONTACT = "https://maker-calendar.test/about"

CRUCIBLE_URL = "https://www.eventbrite.com/o/the-crucible-2700512180"
HUMANMADE_URL = "https://www.eventbrite.com/o/humanmade-57286899753"
CRUCIBLE_ADDRESS = "1260 7th St, Oakland, CA 94607"


# --------------------------------------------------------------------------- helpers


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fetched(
    body: str | bytes,
    *,
    content_type: str | None = "text/html; charset=utf-8",
    status_code: int = 200,
    outcome: Outcome = Outcome.FETCHED,
    label: str = "eventbrite-organizer",
    space_id: str = "the-crucible",
    url: str = CRUCIBLE_URL,
    reason: str | None = None,
) -> FetchResult:
    """A :class:`FetchResult` as the fetch layer would hand one to the adapter."""
    payload = body.encode("utf-8") if isinstance(body, str) else body
    return FetchResult(
        space_id=space_id,
        label=label,
        adapter="nextdata",
        url=url,
        outcome=outcome,
        status_code=status_code,
        content_type=content_type,
        body=payload,
        byte_count=len(payload),
        charset="utf-8",
        reason=reason,
    )


def parse_crucible(**kwargs: Any) -> NextDataParse:
    kwargs.setdefault("today", TODAY)
    kwargs.setdefault("now", NOW)
    return parse_nextdata(fetched(load("crucible-organizer.html")), **kwargs)


def parse_humanmade(**kwargs: Any) -> NextDataParse:
    kwargs.setdefault("today", TODAY)
    kwargs.setdefault("now", NOW)
    return parse_nextdata(
        fetched(
            load("humanmade-organizer.html"), space_id="humanmade", url=HUMANMADE_URL
        ),
        **kwargs,
    )


def page_with(blob: str, *, script_id: str = NEXT_DATA_SCRIPT_ID) -> str:
    """A minimal Next.js shell carrying one ``__NEXT_DATA__`` blob."""
    return (
        "<!DOCTYPE html><html><head><title>t</title>"
        '<script type="application/json" id="app-config">{"locale":"en_US"}</script>'
        f'<script type="application/json" id="{script_id}">{blob}</script>'
        '</head><body><div id="__next"></div></body></html>'
    )


def organizer_page(page_props: dict[str, Any]) -> str:
    """A shell whose ``props.pageProps`` is exactly what the caller says."""
    return page_with(json.dumps({"props": {"pageProps": page_props}, "buildId": "test"}))


def titles(result: NextDataParse) -> list[str]:
    return [event.title or "" for event in result.events]


def by_title(result: NextDataParse, needle: str) -> Any:
    for event in result.events:
        if event.title and needle in event.title:
            return event
    raise AssertionError(f"no event titled {needle!r} in {titles(result)}")


def tours(result: NextDataParse) -> list[Any]:
    return [event for event in result.events if event.title == "Free Crucible Tour"]


class Router:
    """A ``httpx.MockTransport`` handler recording every URL it is asked for."""

    def __init__(self, routes: dict[str, httpx.Response]) -> None:
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        for pattern, response in self.routes.items():
            if pattern in str(request.url):
                return httpx.Response(
                    response.status_code,
                    content=response.content,
                    headers=dict(response.headers),
                )
        return httpx.Response(
            404, content=b"<html>not found</html>", headers={"Content-Type": "text/html"}
        )

    @property
    def urls(self) -> list[str]:
        return [
            str(request.url)
            for request in self.requests
            if request.url.path != "/robots.txt"
        ]

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def html_response(body: str, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        content=body.encode("utf-8"),
        headers={"Content-Type": "text/html; charset=utf-8"},
    )


def noop_sleep(seconds: float) -> None:
    """2 seconds per host. Not in a test suite."""


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def registry() -> Registry:
    return load_registry(env={"MAKER_CALENDAR_CONTACT": TEST_CONTACT})


def nextdata_ref(registry: Registry, space_id: str) -> SourceRef:
    space = registry.space(space_id)
    source = next(s for s in space.sources if s.adapter == "nextdata")
    return SourceRef(space, source)


@pytest.fixture
def crucible_ref(registry: Registry) -> SourceRef:
    return nextdata_ref(registry, "the-crucible")


@pytest.fixture
def humanmade_ref(registry: Registry) -> SourceRef:
    return nextdata_ref(registry, "humanmade")


def make_fetcher(registry: Registry, router: Router, tmp_path: Path) -> Fetcher:
    return Fetcher(
        registry,
        transport=router.transport(),
        raw_dir=tmp_path / "raw",
        sleep=noop_sleep,
        clock=FakeClock(),
    )


# --------------------------------------------------------------------------- the blob


def test_the_organizer_page_carries_no_json_ld_at_all():
    """**The reason this adapter exists.**

    Humanmade's source was registered as ``jsonld``. Since Eventbrite moved
    organizer profiles to Next.js there is not one ``application/ld+json`` block
    on the page, so that source would have returned an empty list every night,
    forever, without ever erroring. A wrong adapter is as silent as a wrong URL.
    """
    for name in ("crucible-organizer.html", "humanmade-organizer.html"):
        assert ld_blocks(load(name)) == ()


def test_the_blob_is_found_by_name_among_several_json_scripts():
    scripts = json_scripts(load("crucible-organizer.html"))
    ids = json_script_ids(scripts)

    assert NEXT_DATA_SCRIPT_ID in ids
    assert "app-config" in ids
    # An anonymous application/json blob is on the page, so "the first one" was
    # never an option.
    assert any(script.is_json and not script.id for script in scripts)
    assert "" not in ids


def test_the_field_map_is_an_embedded_json_field_map_on_a_dotted_path():
    """Issue 0021 built ``embedded_json`` so this could be configuration."""
    assert isinstance(EVENTBRITE_ORGANIZER_FIELD_MAP, FieldMap)
    assert EVENTBRITE_ORGANIZER_FIELD_MAP.items == "props.pageProps.upcomingEvents"
    assert EVENTS_PATH == "props.pageProps.upcomingEvents"
    assert EVENTBRITE_ORGANIZER_FIELD_MAP.start_format == "iso"
    assert EVENTBRITE_ORGANIZER_FIELD_MAP.uid == "id"


# --------------------------------------------------------------------------- the seven


def test_the_populated_organizer_page_yields_its_upcoming_events():
    result = parse_crucible()

    assert result.ok
    assert result.problem is NextDataProblem.NONE
    assert result.organizer == "The Crucible"
    assert result.organizer_id == "2700512180"
    assert result.reported_total == 11
    assert result.item_count == 11
    # 11 listed, one undated, one past the horizon.
    assert result.event_count == 9
    assert result.has_more is False


def test_the_seven_recorded_tours_are_all_here_and_all_inside_the_horizon():
    """2026-08-20 .. 2026-11-19, all "Free Crucible Tour" — the survey's count."""
    inside = [
        event
        for event in tours(parse_crucible())
        if event.start.date() <= dt.date(2026, 11, 19)
    ]

    assert len(inside) == 7
    assert [event.start.date() for event in inside] == [
        dt.date(2026, 8, 20),
        dt.date(2026, 9, 17),
        dt.date(2026, 10, 15),
        dt.date(2026, 10, 29),
        dt.date(2026, 11, 5),
        dt.date(2026, 11, 12),
        dt.date(2026, 11, 19),
    ]


def test_a_start_is_assembled_from_three_separate_fields():
    """``start_date`` + ``start_time`` + ``timezone``, which is the only real
    work this adapter does that ``embedded_json`` could not already do."""
    event = tours(parse_crucible())[0]

    assert event.start == dt.datetime(2026, 8, 20, 11, 0, tzinfo=PDT)
    assert event.tz == "America/Los_Angeles"
    assert event.source_tz == "-07:00"
    assert event.dtstart_form == "tzid"


def test_the_assembled_start_is_correct_on_both_sides_of_the_dst_boundary():
    """DST ends 2026-11-01. The wall clock says 11:00 either way."""
    result = parse_crucible()
    before = [e for e in tours(result) if e.start.date() == dt.date(2026, 10, 29)][0]
    after = [e for e in tours(result) if e.start.date() == dt.date(2026, 11, 5)][0]

    assert before.start == dt.datetime(2026, 10, 29, 11, 0, tzinfo=PDT)
    assert after.start == dt.datetime(2026, 11, 5, 11, 0, tzinfo=PST)
    assert before.start.hour == after.start.hour == 11
    assert (before.source_tz, after.source_tz) == ("-07:00", "-08:00")


def test_the_events_own_timezone_is_honored_not_this_projects_default():
    """One event sits in New York. Converting it in Pacific would be three hours
    wrong and would look entirely plausible in the published calendar."""
    event = by_title(parse_crucible(), "Steel & Story")

    assert event.start == dt.datetime(2026, 9, 24, 18, 30, tzinfo=EDT)
    assert event.tz == "America/New_York"
    assert event.source_tz == "-04:00"
    # And it is a resolvable IANA name, because normalize.day_start_utc and
    # issue 0016's health filter both call ZoneInfo on it outside the
    # per-source try/except.
    assert event.tz != "-04:00"


def test_an_unresolvable_timezone_falls_back_to_pacific_and_is_counted():
    result = parse_nextdata_text(
        organizer_page(
            {
                "upcomingEventsTotal": 1,
                "upcomingEvents": [
                    {
                        "id": "77",
                        "name": "Open Shop",
                        "start_date": "2026-08-20",
                        "start_time": "18:00",
                        "timezone": "Mars/Olympus_Mons",
                    }
                ],
            }
        ),
        today=TODAY,
        now=NOW,
    )

    assert result.ok
    assert result.assumed_timezone_count == 1
    assert result.events[0].tz == DEFAULT_ZONE
    assert "Mars" not in (result.events[0].tz or "")
    assert DEFAULT_ZONE in (result.error or "")


def test_the_entity_in_a_title_is_decoded_on_the_way_out():
    """Script bodies are raw text in HTML; decoding them is ours to do."""
    assert "Steel &amp; Story" in load("crucible-organizer.html")
    assert by_title(parse_crucible(), "Steel & Story").title == (
        "Steel & Story: An Industrial Arts Panel"
    )


# --------------------------------------------------------------------------- venue


def test_primary_venue_address_becomes_the_location():
    """The only source in the registry carrying a real address for this space."""
    assert all(event.location == CRUCIBLE_ADDRESS for event in tours(parse_crucible()))


def test_a_bare_string_address_and_a_multi_line_one_both_resolve():
    result = parse_crucible()

    assert by_title(result, "Steel & Story").location == "150 W 17th St, New York, NY 10011"
    assert by_title(result, "Open House Preview").location == CRUCIBLE_ADDRESS


@pytest.mark.parametrize(
    ("venue", "expected"),
    [
        ({"address": "1260 7th St, Oakland, CA 94607"}, CRUCIBLE_ADDRESS),
        ({"address": {"localized_address_display": CRUCIBLE_ADDRESS}}, CRUCIBLE_ADDRESS),
        (
            {"address": {"localized_multi_line_address_display": ["1260 7th St", "Oakland, CA 94607"]}},
            CRUCIBLE_ADDRESS,
        ),
        (
            {
                "address": {
                    "address_1": "1260 7th St",
                    "city": "Oakland",
                    "region": "CA",
                    "postal_code": "94607",
                }
            },
            "1260 7th St, Oakland, CA, 94607",
        ),
        # No address at all: a named venue is still evidence, and VenuePolicy
        # can act on it.
        ({"name": "The Crucible"}, "The Crucible"),
        ({}, None),
    ],
)
def test_every_eventbrite_address_form_is_read(venue: dict[str, Any], expected: str | None):
    """The survey recorded the *value* and not the sub-key, so guessing one form
    would publish ``None`` for the others — silent-empty, one field down."""
    assert venue_address({"primary_venue": venue}) == expected


# --------------------------------------------------------------------------- zero


def test_a_declared_zero_is_a_clean_empty_result_and_not_a_parse_failure():
    """Humanmade, 2026-08-05, confirmed three ways.

    Both halves are asserted on purpose: the events are empty **and** the parse
    says it understood the page. Either one alone is exactly the ambiguity this
    adapter exists to remove.
    """
    result = parse_humanmade()

    # Empty.
    assert result.events == ()
    assert result.event_count == 0
    assert result.item_count == 0

    # And it parsed fine — the "this is an observation" signal.
    assert result.ok
    assert result.parsed
    assert result.reported_zero
    assert result.problem is NextDataProblem.NONE
    assert result.reported_total == 0
    assert result.has_more is False
    assert result.organizer == "Humanmade"
    assert result.blob_digest  # the blob was really there and really decoded
    assert "observation, not a parse failure" in (result.error or "")


def test_a_declared_zero_is_distinguishable_from_every_way_of_looking_empty():
    """Four pages, zero events each, four different verdicts."""
    declared = parse_humanmade()
    no_blob = parse_nextdata(fetched(load("organizer-no-blob.html")), today=TODAY, now=NOW)
    renamed = parse_nextdata(
        fetched(load("organizer-shape-changed.html")), today=TODAY, now=NOW
    )
    no_total = parse_nextdata_text(
        organizer_page({"upcomingEvents": []}), today=TODAY, now=NOW
    )

    assert all(
        result.event_count == 0 for result in (declared, no_blob, renamed, no_total)
    )
    assert [result.parsed for result in (declared, no_blob, renamed, no_total)] == [
        True,
        False,
        False,
        False,
    ]
    assert [result.problem for result in (no_blob, renamed, no_total)] == [
        NextDataProblem.BLOB_NOT_FOUND,
        NextDataProblem.NO_EVENTS_KEY,
        NextDataProblem.NO_TOTAL,
    ]


def test_a_zero_source_runs_end_to_end_and_reports_ok(
    registry: Registry, humanmade_ref: SourceRef, tmp_path: Path
):
    """The CLI seam. ``require_nonzero_once`` (issue 0016) owns the gate; this
    adapter's job is to hand it a clean zero rather than a failure."""
    router = Router({"/o/humanmade": html_response(load("humanmade-organizer.html"))})
    fetcher = make_fetcher(registry, router, tmp_path)

    record, events = process_source(humanmade_ref, fetcher, horizon_days=120, now=NOW)
    fetcher.close()

    assert record.status == "ok"
    assert record.problem == "none"
    assert record.horizon_count == 0
    assert record.event_count == 0
    assert events == []
    # The registry already says this source has never been non-zero.
    assert humanmade_ref.source.health.require_nonzero_once is True


# --------------------------------------------------------------------------- failures


def test_a_missing_next_data_blob_is_a_reported_failure_not_an_empty_success():
    """A collection page (/cc/<slug>) has no ``__NEXT_DATA__`` at all."""
    result = parse_nextdata(fetched(load("organizer-no-blob.html")), today=TODAY, now=NOW)

    assert not result.ok
    assert not result.parsed
    assert result.problem is NextDataProblem.BLOB_NOT_FOUND
    assert result.event_count == 0
    assert result.reported_total is None
    error = result.error or ""
    # The near-miss id is named, which is what makes this a one-line repair.
    assert "__NEXT_DATA_V2__" in error
    assert "collection-hydration" in error
    assert "nothing scheduled" in error


def test_a_renamed_events_key_is_reported_rather_than_silently_empty():
    """``props.pageProps`` is present and ``upcomingEvents`` is gone."""
    result = parse_nextdata(
        fetched(load("organizer-shape-changed.html")), today=TODAY, now=NOW
    )

    assert not result.ok
    assert result.problem is NextDataProblem.NO_EVENTS_KEY
    assert result.event_count == 0
    error = result.error or ""
    assert "is missing" in error
    # The keys that *are* there, so the repair does not need a second fetch.
    assert "profile" in error and "organizer" in error
    assert "different repair" in error


def test_an_events_key_that_is_not_a_list_is_the_same_report():
    result = parse_nextdata_text(
        organizer_page({"upcomingEvents": {"0": {"id": "1"}}, "upcomingEventsTotal": 1}),
        today=TODAY,
        now=NOW,
    )

    assert result.problem is NextDataProblem.NO_EVENTS_KEY
    assert "not a list" in (result.error or "")


def test_a_missing_total_refuses_to_guess_in_favour_of_zero():
    result = parse_nextdata_text(organizer_page({"upcomingEvents": []}), today=TODAY, now=NOW)

    assert not result.ok
    assert result.problem is NextDataProblem.NO_TOTAL
    assert result.reported_total is None
    assert "will not guess in favour of zero" in (result.error or "")


def test_a_total_that_contradicts_the_array_is_reported_in_both_directions():
    seven_but_empty = parse_nextdata_text(
        organizer_page({"upcomingEvents": [], "upcomingEventsTotal": 7}),
        today=TODAY,
        now=NOW,
    )
    zero_but_populated = parse_nextdata_text(
        organizer_page(
            {
                "upcomingEvents": [
                    {
                        "id": "1",
                        "name": "Free Crucible Tour",
                        "start_date": "2026-08-20",
                        "start_time": "11:00",
                        "timezone": "America/Los_Angeles",
                    }
                ],
                "upcomingEventsTotal": 0,
            }
        ),
        today=TODAY,
        now=NOW,
    )

    for result in (seven_but_empty, zero_but_populated):
        assert result.problem is NextDataProblem.COUNT_DISAGREES
        assert result.event_count == 0
        assert "contradicts itself" in (result.error or "")


def test_a_short_array_with_more_upcoming_is_a_note_and_not_a_failure():
    """``hasMoreUpcoming`` means events sit behind a client-side request. We do
    not guess at an undocumented internal endpoint — we say so and move on."""
    result = parse_nextdata_text(
        organizer_page(
            {
                "upcomingEventsTotal": 12,
                "hasMoreUpcoming": True,
                "upcomingEvents": [
                    {
                        "id": "1",
                        "name": "Free Crucible Tour",
                        "start_date": "2026-08-20",
                        "start_time": "11:00",
                        "timezone": "America/Los_Angeles",
                    }
                ],
            }
        ),
        today=TODAY,
        now=NOW,
    )

    assert result.ok
    assert result.has_more is True
    assert result.event_count == 1
    assert "hasMoreUpcoming is true" in (result.error or "")
    assert "internal endpoint" in (result.error or "")


def test_a_blob_that_is_not_json_is_reported_as_that():
    result = parse_nextdata_text(page_with("{not json at all"), today=TODAY, now=NOW)

    assert result.problem is NextDataProblem.NOT_JSON
    assert "not valid JSON" in (result.error or "")


def test_a_payload_with_no_page_props_is_reported_as_a_shell_change():
    result = parse_nextdata_text(
        page_with(json.dumps({"props": {}, "buildId": "x"})), today=TODAY, now=NOW
    )

    assert result.problem is NextDataProblem.NO_PAGE_PROPS
    assert "page shell changed" in (result.error or "")


def test_events_with_no_usable_start_at_all_are_a_schema_change():
    result = parse_nextdata_text(
        organizer_page(
            {
                "upcomingEventsTotal": 2,
                "upcomingEvents": [
                    {"id": "1", "name": "A", "starts_at": "2026-08-20T11:00"},
                    {"id": "2", "name": "B", "starts_at": "2026-08-27T11:00"},
                ],
            }
        ),
        today=TODAY,
        now=NOW,
    )

    assert result.problem is NextDataProblem.NO_DATES
    assert result.item_count == 2
    assert "schema change" in (result.error or "")


def test_transport_and_status_failures_never_look_like_an_empty_organizer():
    not_modified = parse_nextdata(
        fetched("", outcome=Outcome.NOT_MODIFIED, status_code=304), today=TODAY, now=NOW
    )
    http_error = parse_nextdata(
        fetched("<html>gone</html>", status_code=404), today=TODAY, now=NOW
    )
    wrong_type = parse_nextdata(
        fetched('{"props":{}}', content_type="application/json"), today=TODAY, now=NOW
    )
    empty = parse_nextdata(fetched("", status_code=200), today=TODAY, now=NOW)

    assert not_modified.problem is NextDataProblem.NOT_MODIFIED
    assert http_error.problem is NextDataProblem.HTTP_ERROR
    assert wrong_type.problem is NextDataProblem.WRONG_CONTENT_TYPE
    assert empty.problem is NextDataProblem.EMPTY_BODY
    assert all(
        result.event_count == 0 and not result.parsed
        for result in (not_modified, http_error, wrong_type, empty)
    )
    assert "HTTP 200 is not success" in (wrong_type.error or "")


# --------------------------------------------------------------------------- dates


def test_a_start_date_with_no_start_time_is_undated_and_counted_never_midnight():
    """Publishing a 12:00 AM tour would be wrong data that looks like good data."""
    result = parse_crucible()

    assert "Members' Night" not in titles(result)
    assert result.undated_item_count == 1
    assert result.undated_ids == ("1901000000011",)
    assert "were skipped" in (result.error or "")
    assert assemble_start({"start_date": "2026-10-08"}) is None
    assert assemble_start({"start_date": "2026-10-08", "start_time": "11:00"}) == (
        "2026-10-08T11:00"
    )


def test_the_horizon_clips_on_the_events_own_local_day():
    """One tour is 2027-01-21, past the 120-day window that ends 2026-12-03."""
    clipped = parse_crucible()
    wide = parse_crucible(horizon_days=365)

    assert clipped.window_end == HORIZON_END
    assert max(event.start.date() for event in clipped.events) == dt.date(2026, 11, 19)
    assert clipped.event_count == 9
    assert wide.event_count == 10
    assert dt.date(2027, 1, 21) in [event.start.date() for event in wide.events]
    # The pre-clip count is reported separately, so a shrinking horizon is never
    # mistaken for a shrinking source.
    assert clipped.vevent_count == wide.vevent_count == 10


# --------------------------------------------------------------------------- uids


def test_uids_are_the_event_id_and_the_start_epoch_and_are_stable():
    first = parse_crucible()
    again = parse_crucible(now=NOW + dt.timedelta(hours=9))

    event = tours(first)[0]
    expected_epoch = int(dt.datetime(2026, 8, 20, 11, 0, tzinfo=PDT).timestamp())
    assert event.uid == f"1901000000001:{expected_epoch}"
    # Nothing about the scrape is in it: parsing again nine hours later gives
    # byte-identical UIDs, which is what keeps subscribers from seeing every
    # event as new every night.
    assert [e.uid for e in first.events] == [e.uid for e in again.events]


def test_eight_events_sharing_one_title_get_eight_distinct_uids():
    """Every "Free Crucible Tour" is the same words and a different event."""
    wide = parse_crucible(horizon_days=365)
    all_tours = tours(wide)

    assert len(all_tours) == 8
    assert len({event.title for event in all_tours}) == 1
    assert len({event.uid for event in all_tours}) == 8


def test_an_event_with_no_id_is_counted_and_left_for_normalize_to_name():
    result = parse_crucible()
    orphan = by_title(result, "Open House Preview")

    assert orphan.uid is None
    assert result.missing_id_count == 1
    assert "falls back to" in (result.error or "")


def test_normalize_namespaces_the_uid_and_keeps_the_address(registry: Registry):
    space = registry.space("the-crucible")
    normalization = normalize_ics(
        parse_crucible(), space=space, source_label="eventbrite-organizer", now=NOW
    )
    events = list(normalization.events)

    assert len(events) == 9
    tour = next(event for event in events if event.title == "Free Crucible Tour")
    expected_epoch = int(dt.datetime(2026, 8, 20, 11, 0, tzinfo=PDT).timestamp())
    assert tour.uid == f"the-crucible:1901000000001:{expected_epoch}"
    assert tour.start_utc == dt.datetime(2026, 8, 20, 18, 0, tzinfo=dt.timezone.utc)
    assert tour.tz == "America/Los_Angeles"
    assert tour.address == CRUCIBLE_ADDRESS
    # The orphan without an id still gets a stable UID: normalize's
    # sha1(space_id + start_utc + title)[:16], which takes the space as an
    # input rather than as a prefix.
    orphan = next(event for event in events if event.title == "Open House Preview")
    assert len(orphan.uid) == 16
    assert ":" not in orphan.uid


# --------------------------------------------------------------------------- helpers


def test_event_zone_reads_the_items_own_timezone():
    name, zone, assumed = event_zone({"timezone": "America/New_York"})

    assert name == "America/New_York"
    assert zone is not None
    assert assumed is False


def test_event_zone_says_so_when_it_is_assuming():
    for item in ({}, {"timezone": ""}, {"timezone": "Nowhere/Nothing"}):
        name, zone, assumed = event_zone(item)
        assert (name, assumed) == (DEFAULT_ZONE, True)
        assert zone is not None


# --------------------------------------------------------------------------- wiring


def test_parse_is_the_registry_entry_point():
    """``sources.yaml``'s ``adapter: nextdata`` resolves to ``parse``."""
    assert parse is parse_nextdata


def test_the_dispatch_table_wires_the_adapter_with_no_extra_appetites():
    entry = ADAPTERS["nextdata"]

    assert entry.implemented
    assert entry.parse is parse_nextdata
    assert entry.issue == "0023"
    # One page, no follow-ups, and nothing to configure: the blob name and the
    # payload paths are what make this adapter Eventbrite's.
    assert entry.paginates is False
    assert entry.needs_source is False
    assert "nextdata" in implemented_adapters()


def test_both_enabled_eventbrite_organizer_sources_are_runnable_now(
    crucible_ref: SourceRef, humanmade_ref: SourceRef
):
    assert crucible_ref.source.url == CRUCIBLE_URL
    assert humanmade_ref.source.url == HUMANMADE_URL
    assert is_runnable(crucible_ref)
    assert is_runnable(humanmade_ref)


def test_the_box_shop_organizer_is_registered_and_deliberately_disabled(
    registry: Registry,
):
    """0 upcoming on 2026-08-05, so it stays documented and unfetched."""
    source = next(
        s for s in registry.space("the-box-shop").sources if s.adapter == "nextdata"
    )

    assert source.enabled is False
    assert not is_runnable(SourceRef(registry.space("the-box-shop"), source))


def test_fetch_nextdata_drives_the_whole_flow_in_exactly_one_request(
    registry: Registry, crucible_ref: SourceRef, tmp_path: Path
):
    """Eventbrite's terms restrict scraping. One public page, one GET, no
    ``?tab=past`` (it answers with the same blob) and no guess at the XHR
    behind ``hasMoreUpcoming``."""
    router = Router({"/o/the-crucible": html_response(load("crucible-organizer.html"))})
    fetcher = make_fetcher(registry, router, tmp_path)

    result = fetch_nextdata(fetcher, crucible_ref, today=TODAY, now=NOW, horizon_days=120)
    fetcher.close()

    assert result.ok
    assert result.event_count == 9
    assert len(router.urls) == 1
    assert router.urls[0] == CRUCIBLE_URL
    assert "?" not in router.urls[0]


def test_process_source_publishes_the_tours_with_their_address(
    registry: Registry, crucible_ref: SourceRef, tmp_path: Path
):
    router = Router({"/o/the-crucible": html_response(load("crucible-organizer.html"))})
    fetcher = make_fetcher(registry, router, tmp_path)

    record, events = process_source(crucible_ref, fetcher, horizon_days=120, now=NOW)
    fetcher.close()

    assert record.status == "ok"
    assert record.problem == "none"
    assert record.raw_count == 10  # starts seen, pre-clip
    assert record.horizon_count == 9  # the health-gate number
    assert record.event_count == 9
    assert len(events) == 9
    assert all(event.tz in ("America/Los_Angeles", "America/New_York") for event in events)
    assert (
        sum(1 for event in events if event.address == CRUCIBLE_ADDRESS) == 8
    )


def test_a_drifted_page_fails_the_source_rather_than_emptying_it(
    registry: Registry, crucible_ref: SourceRef, tmp_path: Path
):
    router = Router({"/o/the-crucible": html_response(load("organizer-shape-changed.html"))})
    fetcher = make_fetcher(registry, router, tmp_path)

    record, events = process_source(crucible_ref, fetcher, horizon_days=120, now=NOW)
    fetcher.close()

    assert record.status == "failed"
    assert record.problem == "no_events_key"
    assert events == []
