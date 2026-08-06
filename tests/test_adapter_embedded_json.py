"""Tests for the embedded-JSON adapter (issue 0021).

**No test here touches the network.** Issue 0001 — the bot about page that
``$MAKER_CALENDAR_CONTACT`` points at — is still open, so a live request would
go out under a User-Agent naming a page that does not resolve. Every request in
this file goes through ``httpx.MockTransport`` and every payload is a
hand-authored fixture under ``tests/fixtures/embedded_json/``.

The fixture is a **representative** ``/course-search/``, not a copy of the live
216-course blob: thirteen courses that between them exercise every case the
2026-08-05 survey recorded, with exact counts asserted, is worth more than 216
that exercise one. ``today`` is pinned to the survey date so the horizon counts
below are exact rather than approximate.

Seven things are being defended.

**The blob is found by name, among several.** The page carries five other
``application/json`` scripts plus an ``ld+json`` one, and one of the others is
anonymous. Picking the first would be a guess that happened to work.

**Three ways of not finding it are three different reports.** No ``script_id``
configured at all, the page no longer carrying that ``id``, and the blob being
present but not JSON are separate :class:`EmbeddedJsonProblem` values, because
they have separate fixes and "0 events" from each means something different at
09:00.

**Epoch seconds decode to the right local wall clock, on both sides of a DST
boundary.** ``1786064400`` is 08/06/26 6:00 PM PDT and ``1794362400`` is
11/10/26 6:00 PM PST — verified against the product page's ``Class.Date``
attribute to the minute.

**One ``start_date`` is one RUN of a course.** A 20-hour, 5-week class with one
``start_date`` is one event; ``Woodworking I`` with three is three, with three
distinct UIDs.

**``hours`` never becomes ``DTEND``.** It is total course hours;
``Woodworking I`` is 40 of them and would render a two-day block.

**Empty ``start_dates`` are skipped and counted**, as is the epoch-0 sentinel.

**Entities are decoded on the way out.** The blob stores ``Glass Fusing &amp;
Slumping``; the calendar publishes ``Glass Fusing & Slumping``, and issue 0010's
filters match either spelling.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from pipeline.adapters.embedded_json import (
    CRUCIBLE_FIELD_MAP,
    DEFAULT_ZONE,
    EmbeddedJsonEvent,
    EmbeddedJsonParse,
    EmbeddedJsonProblem,
    FieldMap,
    fetch_embedded_json,
    find_items,
    find_script,
    json_script_ids,
    json_scripts,
    parse,
    parse_embedded_json,
    parse_embedded_json_text,
    parse_epoch,
    resolve_path,
)
from pipeline.cli import ADAPTERS, implemented_adapters, is_runnable, process_source
from pipeline.config import Filters, Registry, SourceRef, load_registry
from pipeline.fetch import FetchResult, Fetcher, Outcome
from pipeline.filters import filter_normalization
from pipeline.normalize import normalize_ics

FIXTURES = Path(__file__).parent / "fixtures" / "embedded_json"

#: The source-survey date. Every fixture is authored relative to it.
TODAY = dt.date(2026, 8, 5)
NOW = dt.datetime(2026, 8, 5, 3, 15, tzinfo=dt.timezone.utc)
HORIZON_END = dt.date(2026, 12, 3)  # TODAY + 120 days, inclusive

PACIFIC = ZoneInfo("America/Los_Angeles")
PDT = dt.timezone(dt.timedelta(hours=-7))
PST = dt.timezone(dt.timedelta(hours=-8))

TEST_CONTACT = "https://maker-calendar.test/about"

CRUCIBLE_URL = "https://www.thecrucible.org/course-search/"
SHOP_URL = "https://www.thecrucible.org/shop/"
SCRIPT_ID = "ac-course-data"

#: The two epochs the survey verified against the product page, to the minute.
GLASS_FUSING_EPOCH = 1786064400  # 08/06/26 6:00 PM PDT
BIKE_EPOCH = 1790902800  # 10/01/26 6:00 PM PDT
#: A start on the far side of the 2026-11-01 DST transition.
NEON_EPOCH = 1794362400  # 11/10/26 6:00 PM PST


# --------------------------------------------------------------------------- helpers


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fetched(
    body: str | bytes,
    *,
    content_type: str | None = "text/html; charset=UTF-8",
    status_code: int = 200,
    outcome: Outcome = Outcome.FETCHED,
    label: str = "course-catalog-blob",
    space_id: str = "the-crucible",
    url: str = CRUCIBLE_URL,
    reason: str | None = None,
) -> FetchResult:
    """A :class:`FetchResult` as the fetch layer would hand one to the adapter."""
    payload = body.encode("utf-8") if isinstance(body, str) else body
    return FetchResult(
        space_id=space_id,
        label=label,
        adapter="embedded_json",
        url=url,
        outcome=outcome,
        status_code=status_code,
        content_type=content_type,
        body=payload,
        byte_count=len(payload),
        charset="utf-8",
        reason=reason,
    )


def parse_crucible(**kwargs: Any) -> EmbeddedJsonParse:
    kwargs.setdefault("today", TODAY)
    kwargs.setdefault("now", NOW)
    kwargs.setdefault("script_id", SCRIPT_ID)
    return parse_embedded_json(fetched(load("crucible-course-search.html")), **kwargs)


def titles(result: EmbeddedJsonParse) -> list[str]:
    return [event.title or "" for event in result.events]


def by_title(result: EmbeddedJsonParse, needle: str) -> EmbeddedJsonEvent:
    for event in result.events:
        if event.title and needle in event.title:
            return event  # type: ignore[return-value]
    raise AssertionError(f"no event titled {needle!r} in {titles(result)}")


def all_titled(result: EmbeddedJsonParse, needle: str) -> list[EmbeddedJsonEvent]:
    return [
        event  # type: ignore[misc]
        for event in result.events
        if event.title and needle in event.title
    ]


def page_with(blob: str, *, script_id: str = SCRIPT_ID, script_type: str = "application/json") -> str:
    """A minimal server-rendered page carrying one named blob."""
    return (
        "<!DOCTYPE html><html><head><title>t</title>"
        '<script type="application/json" id="wp-block-settings">{"a":1}</script>'
        f'<script type="{script_type}" id="{script_id}">{blob}</script>'
        "</head><body><div id=\"ac-course-search-root\"></div></body></html>"
    )


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
        headers={"Content-Type": "text/html; charset=UTF-8"},
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


@pytest.fixture
def crucible_ref(registry: Registry) -> SourceRef:
    space = registry.space("the-crucible")
    source = next(s for s in space.sources if s.adapter == "embedded_json")
    return SourceRef(space, source)


def make_fetcher(registry: Registry, router: Router, tmp_path: Path) -> Fetcher:
    return Fetcher(
        registry,
        transport=router.transport(),
        raw_dir=tmp_path / "raw",
        sleep=noop_sleep,
        clock=FakeClock(),
    )


# --------------------------------------------------------------------------- the blob


def test_the_named_blob_is_found_among_several_json_blobs_on_the_page():
    """Five other ``application/json`` scripts share this page."""
    document = load("crucible-course-search.html")
    script, scripts = find_script(document, SCRIPT_ID)

    assert script is not None
    assert script.id == SCRIPT_ID
    assert script.is_json
    assert json.loads(script.text)[0]["id"] == 210220
    # More than one JSON blob, so "the first one" was never an option.
    assert len([s for s in scripts if s.is_json]) >= 5


def test_every_json_blob_id_is_reported_for_diagnostics():
    ids = json_script_ids(json_scripts(load("crucible-course-search.html")))

    assert SCRIPT_ID in ids
    assert "wp-block-settings" in ids
    assert "fusion-slider-data" in ids
    assert "popup-maker-settings" in ids


def test_the_ld_json_block_is_not_mistaken_for_the_named_blob():
    """``application/ld+json`` is ``jsonld``'s business, not this adapter's."""
    scripts = json_scripts(load("crucible-course-search.html"))
    ld = [s for s in scripts if s.type.startswith("application/ld+json")]

    assert ld and not ld[0].is_json


def test_an_anonymous_json_blob_is_never_picked():
    """The page carries a ``type="application/json"`` script with no ``id``."""
    scripts = json_scripts(load("crucible-course-search.html"))

    assert any(s.is_json and not s.id for s in scripts)
    assert "" not in json_script_ids(scripts)


def test_the_raw_blob_still_carries_the_html_entity():
    """Script bodies are raw text in HTML — decoding them is ours to do."""
    script, _ = find_script(load("crucible-course-search.html"), SCRIPT_ID)

    assert script is not None
    assert "Glass Fusing &amp; Slumping" in script.text
    assert "Glass Fusing & Slumping" not in script.text


# --------------------------------------------------------------------------- failures


def test_a_missing_script_id_is_a_reported_failure_not_an_empty_success():
    result = parse_embedded_json(
        fetched(load("crucible-course-search.html")), today=TODAY, now=NOW
    )

    assert not result.ok
    assert result.problem is EmbeddedJsonProblem.NO_SCRIPT_ID
    assert result.event_count == 0
    assert "script_id" in (result.error or "")
    assert "ac-course-data" in (result.error or "")


def test_a_page_without_the_named_blob_is_reported_and_says_what_is_there():
    result = parse_embedded_json(
        fetched(load("crucible-no-blob.html")),
        script_id=SCRIPT_ID,
        today=TODAY,
        now=NOW,
    )

    assert not result.ok
    assert result.problem is EmbeddedJsonProblem.SCRIPT_NOT_FOUND
    assert result.event_count == 0
    assert "drifted source" in (result.error or "")
    # The near-miss id is named, which is what makes this a one-line repair.
    assert "ac-course-data-v2" in (result.error or "")
    assert "ac-course-data-v2" in result.script_ids_seen


def test_a_two_hundred_with_html_and_no_matching_blob_is_never_an_empty_catalog():
    """HTTP 200 is not success. A real page with the catalog gone must not
    reach ``health.json`` as a healthy source with nothing on."""
    result = parse_embedded_json(
        fetched(load("crucible-no-blob.html"), status_code=200),
        script_id=SCRIPT_ID,
        today=TODAY,
        now=NOW,
    )

    assert not result.ok
    assert bool(result) is False  # __bool__ is the verdict, not the count
    assert result.event_count == 0
    assert result.problem is EmbeddedJsonProblem.SCRIPT_NOT_FOUND


def test_a_same_id_script_with_the_wrong_type_says_so():
    document = page_with("[]", script_type="text/javascript")
    result = parse_embedded_json_text(document, script_id=SCRIPT_ID, today=TODAY, now=NOW)

    assert result.problem is EmbeddedJsonProblem.SCRIPT_NOT_FOUND
    assert "'text/javascript'" in (result.error or "")


def test_a_malformed_blob_is_reported_as_not_json():
    result = parse_embedded_json_text(
        page_with('[{"id": 1, "start_dates": [178606'), script_id=SCRIPT_ID, today=TODAY, now=NOW
    )

    assert result.problem is EmbeddedJsonProblem.NOT_JSON
    assert result.blob_bytes > 0


def test_a_changed_shape_is_reported_as_no_items_not_as_no_events():
    result = parse_embedded_json_text(
        page_with('{"courses": {"210220": {"title": "Glass"}}}'),
        script_id=SCRIPT_ID,
        today=TODAY,
        now=NOW,
    )

    assert result.problem is EmbeddedJsonProblem.NO_ITEMS
    assert "shape changed" in (result.error or "")


def test_a_catalog_with_no_usable_dates_at_all_is_reported():
    blob = json.dumps(
        [
            {"id": 1, "title": "A", "start_dates": []},
            {"id": 2, "title": "B", "start_dates": [0]},
        ]
    )
    result = parse_embedded_json_text(
        page_with(blob), script_id=SCRIPT_ID, today=TODAY, now=NOW
    )

    assert result.problem is EmbeddedJsonProblem.NO_DATES
    assert "schema change" in (result.error or "")


def test_a_404_is_an_http_error_not_an_empty_catalog():
    result = parse_embedded_json(
        fetched("<html>not found</html>", status_code=404),
        script_id=SCRIPT_ID,
        today=TODAY,
        now=NOW,
    )

    assert result.problem is EmbeddedJsonProblem.HTTP_ERROR
    assert "HTTP 404" in (result.error or "")


def test_a_304_is_not_modified_and_says_to_reuse_the_stored_events():
    result = parse_embedded_json(
        fetched(b"", status_code=304, outcome=Outcome.NOT_MODIFIED),
        script_id=SCRIPT_ID,
        today=TODAY,
        now=NOW,
    )

    assert result.problem is EmbeddedJsonProblem.NOT_MODIFIED
    assert "reuse the stored events" in (result.error or "")


def test_a_body_that_is_not_the_registered_page_is_refused():
    result = parse_embedded_json(
        fetched("BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n", content_type="text/calendar"),
        script_id=SCRIPT_ID,
        today=TODAY,
        now=NOW,
    )

    assert result.problem is EmbeddedJsonProblem.WRONG_CONTENT_TYPE
    assert "HTTP 200 is not success" in (result.error or "")


# --------------------------------------------------------------------------- dates


def test_epoch_seconds_decode_to_the_documented_wall_clock():
    """``1786064400`` → ``08/06/26 6:00 PM``, verified against ``Class.Date``."""
    event = by_title(parse_crucible(), "Glass Fusing")

    assert event.start == dt.datetime(2026, 8, 6, 18, 0, tzinfo=PDT)
    assert event.start.utcoffset() == dt.timedelta(hours=-7)
    assert event.start_epoch == GLASS_FUSING_EPOCH


def test_the_second_documented_epoch_decodes_too():
    """``1790902800`` → ``10/01/26 6:00 PM``."""
    event = by_title(parse_crucible(), "Bike Repair")

    assert event.start == dt.datetime(2026, 10, 1, 18, 0, tzinfo=PDT)
    assert event.start_epoch == BIKE_EPOCH


def test_an_epoch_across_the_dst_boundary_is_still_six_in_the_evening():
    """2026-11-10 is after the 11-01 transition: same 6 PM, different offset."""
    event = by_title(parse_crucible(), "Neon")

    assert event.start_epoch == NEON_EPOCH
    assert event.start == dt.datetime(2026, 11, 10, 18, 0, tzinfo=PST)
    assert event.start.utcoffset() == dt.timedelta(hours=-8)
    assert event.start.astimezone(dt.timezone.utc) == dt.datetime(
        2026, 11, 11, 2, 0, tzinfo=dt.timezone.utc
    )
    # The whole point: PDT and PST both print 6:00 PM to a student.
    assert by_title(parse_crucible(), "Glass Fusing").start.hour == event.start.hour


def test_the_zone_is_a_resolvable_iana_name_not_an_offset():
    """``normalize.day_start_utc`` and issue 0016's health filter call
    ``ZoneInfo`` on this, and that filter runs outside the per-source
    try/except — an offset string would take the run down."""
    for event in parse_crucible().events:
        assert event.tz == DEFAULT_ZONE
        assert ZoneInfo(event.tz) == PACIFIC


def test_a_quoted_epoch_is_still_an_epoch():
    """``Wheel Throwing II`` carries ``"1796353200"`` as a string."""
    event = by_title(parse_crucible(), "Wheel Throwing")

    assert event.start == dt.datetime(2026, 12, 3, 19, 0, tzinfo=PST)


def test_the_epoch_zero_sentinel_is_dropped_and_counted():
    """``01/01/70 12:00 am`` exists in this catalog's other half. Drop it
    explicitly rather than letting a horizon check absorb it."""
    result = parse_crucible()

    assert result.bad_date_count == 1
    assert "Enameling" not in " ".join(titles(result))
    assert "0 sentinel" in (result.error or "")


@pytest.mark.parametrize(
    "value",
    [0, -1, "", "  ", "not-a-number", None, True, [], {}, 4102444801],
)
def test_parse_epoch_refuses_anything_it_cannot_verify(value: Any):
    assert parse_epoch(value, zone=PACIFIC) == (None, None)


def test_parse_epoch_accepts_the_shapes_that_actually_occur():
    for value in (GLASS_FUSING_EPOCH, float(GLASS_FUSING_EPOCH), str(GLASS_FUSING_EPOCH)):
        start, epoch = parse_epoch(value, zone=PACIFIC)
        assert epoch == GLASS_FUSING_EPOCH
        assert start == dt.datetime(2026, 8, 6, 18, 0, tzinfo=PDT)


# --------------------------------------------------------------------------- counting


def test_the_representative_catalog_parses_with_exact_counts():
    result = parse_crucible()

    assert result.ok
    assert result.problem is EmbeddedJsonProblem.NONE
    assert result.script_id == SCRIPT_ID
    assert result.item_count == 13  # courses in the blob
    assert result.offering_count == 13  # start_dates entries seen
    assert result.vevent_count == result.offering_count
    assert result.undated_item_count == 2
    assert result.bad_date_count == 1
    assert result.dated_item_count == 11
    assert result.event_count == 9  # inside the 120-day horizon
    assert result.blob_bytes > 4000
    assert len(result.blob_digest) == 16


def test_courses_with_an_empty_start_dates_array_are_skipped_and_counted():
    """Nine of the live 216. Skipped, never emitted dateless, always counted."""
    result = parse_crucible()

    assert result.undated_item_count == 2
    assert set(result.undated_ids) == {"210444", "210445"}
    assert "Metal Furniture Fabrication" not in " ".join(titles(result))
    assert "Foundry Fundamentals" not in " ".join(titles(result))
    assert "empty 'start_dates' array and were skipped" in (result.error or "")


def test_a_course_with_three_start_dates_yields_three_events_with_distinct_uids():
    runs = all_titled(parse_crucible(), "Woodworking I")

    assert len(runs) == 3
    assert [event.start_epoch for event in runs] == [1787241600, 1789660800, 1793289600]
    assert len({event.uid for event in runs}) == 3
    # Three runs of one course, so everything except the date is shared.
    assert {event.item_id for event in runs} == {"210101"}
    assert {event.title for event in runs} == {"Woodworking I"}


def test_a_five_week_course_is_one_event_not_one_per_meeting():
    """20 hours over Tuesdays and Thursdays — roughly ten meetings, one entry.

    That collapse is the *good* case, and it is what a merged calendar wants.
    """
    runs = all_titled(parse_crucible(), "Glass Fusing")

    assert len(runs) == 1
    assert runs[0].hours == 20.0
    assert runs[0].days_text == "Tuesday; Thursday"


# --------------------------------------------------------------------------- UIDs


def test_uid_is_id_colon_start_epoch():
    event = by_title(parse_crucible(), "Glass Fusing")

    assert event.uid == f"210220:{GLASS_FUSING_EPOCH}"


def test_uids_are_stable_across_runs():
    """A UID that churns makes every subscriber see every event as new."""
    first = parse_crucible()
    second = parse_crucible(now=NOW + dt.timedelta(days=0, hours=6))

    assert [event.uid for event in first.events] == [event.uid for event in second.events]


def test_uid_reaches_normalize_as_space_id_plus_id_plus_epoch(registry: Registry):
    space = registry.space("the-crucible")
    normalization = normalize_ics(
        parse_crucible(), space=space, source_label="course-catalog-blob", now=NOW
    )

    uids = {event.uid for event in normalization.events}
    assert f"the-crucible:210220:{GLASS_FUSING_EPOCH}" in uids
    assert len(uids) == len(normalization.events) == 9


# --------------------------------------------------------------------------- hours


def test_hours_is_never_used_for_dtend():
    """``Woodworking I`` is 40 total course hours across a week of meetings.

    ``DTSTART + hours`` renders a two-day block in every calendar client.
    """
    runs = all_titled(parse_crucible(), "Woodworking I")

    assert [event.hours for event in runs] == [40.0, 40.0, 40.0]
    assert all(event.end is None for event in runs)


def test_no_event_anywhere_carries_a_derived_end():
    result = parse_crucible()

    assert all(event.end is None for event in result.events)
    assert all(event.all_day is False for event in result.events)
    assert all(event.days == 1 for event in result.events)


# --------------------------------------------------------------------------- entities


def test_html_entities_are_decoded_in_the_emitted_title():
    """The blob stores ``Glass Fusing &amp; Slumping``, never ``&``."""
    event = by_title(parse_crucible(), "Glass Fusing")

    assert event.title == "Glass Fusing & Slumping Lab – 5 weeks"
    assert "&amp;" not in event.title
    assert event.categories == ("Glass Fusing & Slumping",)


def test_entities_are_decoded_in_every_field_that_carries_one():
    result = parse_crucible()

    assert by_title(result, "Neon").categories == ("Neon & Light",)
    assert by_title(result, "Neon").title == "Neon & Light I"


def test_filters_match_the_decoded_department_written_either_way(registry: Registry):
    """Issue 0010's filters are entity-insensitive in both directions, so the
    registry may write either spelling — but what we *emit* is the decoded one.
    """
    space = registry.space("the-crucible")
    normalization = normalize_ics(
        parse_crucible(), space=space, source_label="course-catalog-blob", now=NOW
    )

    decoded = filter_normalization(
        normalization, Filters(categories_exclude=["Glass Fusing & Slumping"])
    )
    escaped = filter_normalization(
        normalization, Filters(categories_exclude=["Glass Fusing &amp; Slumping"])
    )

    assert decoded.kept_count == escaped.kept_count == 8
    assert decoded.dropped_count == escaped.dropped_count == 1


def test_the_optional_registry_filters_do_what_the_comment_says(registry: Registry):
    """``categories_exclude: ["Bike Shop"]`` and ``title_excludes: ["Youth "]``."""
    space = registry.space("the-crucible")
    normalization = normalize_ics(
        parse_crucible(), space=space, source_label="course-catalog-blob", now=NOW
    )

    filtered = filter_normalization(
        normalization,
        Filters(categories_exclude=["Bike Shop"], title_excludes=["Youth "]),
    )

    assert filtered.kept_count == 7
    assert filtered.dropped_count == 2


# --------------------------------------------------------------------------- horizon


def test_horizon_clipping_drops_the_past_and_the_far_future():
    result = parse_crucible()

    assert "Blacksmithing I" not in titles(result)  # 2026-07-01, before today
    assert "Welding I" not in titles(result)  # 2027-03-15, past the horizon
    assert result.window_start == TODAY
    assert result.window_end == HORIZON_END


def test_the_horizon_boundary_is_inclusive_on_the_last_day():
    result = parse_crucible()

    # 2026-12-03 19:00 is the last day of the window; 2026-12-04 09:00 is not.
    assert by_title(result, "Wheel Throwing").start.date() == HORIZON_END
    assert "Kinetics" not in " ".join(titles(result))


def test_a_shorter_horizon_clips_more():
    result = parse_crucible(horizon_days=60)

    assert result.window_end == TODAY + dt.timedelta(days=60)
    assert result.offering_count == 13  # the raw count does not move
    assert result.event_count == 5


def test_events_are_returned_in_start_order():
    starts = [event.start for event in parse_crucible().events]

    assert starts == sorted(starts)


# --------------------------------------------------------------------------- fields


def test_location_is_left_empty_for_venue_policy_to_fill():
    """The catalog has no location field of any kind. ``address_override`` is
    mandatory for this space and :class:`VenuePolicy` owns that decision."""
    assert all(event.location is None for event in parse_crucible().events)


def test_price_is_source_text_and_never_a_float():
    result = parse_crucible()

    assert by_title(result, "Glass Fusing").price == "$395 / $355.50 members"
    # "sliding scale $10-30" is a real value in this dataset. float() would
    # turn it into a wrong number, or into None.
    assert by_title(result, "Jewelry Lab").price == "sliding scale $10-30"


def test_a_sold_out_class_is_still_a_real_public_event():
    """27 live courses are ``is_in_stock: false`` with future dates."""
    event = by_title(parse_crucible(), "Jewelry Lab")

    assert event.in_stock is False
    assert event in parse_crucible().events


def test_the_descriptive_fields_survive_for_diagnostics_and_filters():
    event = by_title(parse_crucible(), "Youth Blacksmithing")

    assert event.audience == "Ages 12-18"
    assert event.level == "Entry Level"
    assert event.course_format == "Weeklong"
    assert event.time_of_day == "All Day"
    assert event.url.endswith("/youth-blacksmithing-immersion/")
    assert event.script_id == SCRIPT_ID


def test_the_malformed_day_value_is_kept_verbatim():
    """One live value is ``"Monday, Tuesday, Wednesday, Thursday "`` — commas
    and a trailing space, which a naive ``split("; ")`` mis-parses."""
    event = all_titled(parse_crucible(), "Woodworking I")[0]

    assert event.days_text == "Monday, Tuesday, Wednesday, Thursday"


# --------------------------------------------------------------------------- mirror


def test_the_shop_mirror_carries_a_byte_identical_blob():
    """``/shop/`` is the same 132 KB blob. Registering both would double every
    event and then hand the mess to dedupe."""
    catalog = parse_crucible()
    mirror = parse_embedded_json(
        fetched(load("crucible-course-search.html"), url=SHOP_URL),
        script_id=SCRIPT_ID,
        today=TODAY,
        now=NOW,
    )

    assert mirror.blob_digest == catalog.blob_digest
    assert [e.uid for e in mirror.events] == [e.uid for e in catalog.events]


# --------------------------------------------------------------------------- general


def test_resolve_path_walks_nested_objects_and_list_indices():
    """What makes ``nextdata`` reimplementable on top of this adapter."""
    payload = {"props": {"pageProps": {"upcomingEvents": [{"name": "Free Tour"}]}}}

    assert resolve_path(payload, "props.pageProps.upcomingEvents.0.name") == "Free Tour"
    assert resolve_path(payload, "props.missing.name") is None
    assert resolve_path(payload, "props.pageProps.upcomingEvents.9") is None


def test_a_dotted_items_path_reads_a_next_data_shaped_blob():
    payload = {"props": {"pageProps": {"upcomingEvents": [{"id": 1}, {"id": 2}]}}}

    assert find_items(payload, "props.pageProps.upcomingEvents") == [{"id": 1}, {"id": 2}]
    # An explicit path is never "helped" by a search that finds another list.
    assert find_items(payload, "props.pageProps.somethingElse") == []


def test_an_explicit_field_map_can_read_another_shape():
    blob = json.dumps(
        {
            "catalog": [
                {
                    "pk": 77,
                    "name": "Intro to Casting",
                    "link": "https://example.test/casting",
                    "sessions": ["2026-09-10T18:30:00-07:00"],
                    "track": "Foundry",
                }
            ]
        }
    )
    result = parse_embedded_json_text(
        page_with(blob),
        script_id=SCRIPT_ID,
        field_map=FieldMap(
            items="catalog",
            uid="pk",
            title="name",
            url="link",
            starts="sessions",
            start_format="iso",
            categories="track",
            price="nope",
            member_price=None,
            image=None,
            in_stock=None,
            hours=None,
            audience=None,
            level=None,
            item_format=None,
            time_of_day=None,
            days_text=None,
        ),
        today=TODAY,
        now=NOW,
    )

    assert result.ok
    assert result.event_count == 1
    event = result.events[0]
    assert event.title == "Intro to Casting"
    assert event.start == dt.datetime(2026, 9, 10, 18, 30, tzinfo=PDT)
    expected_epoch = int(dt.datetime(2026, 9, 10, 18, 30, tzinfo=PDT).timestamp())
    assert event.uid == f"77:{expected_epoch}"
    assert event.categories == ("Foundry",)


def test_the_default_field_map_is_the_crucible_shape():
    assert CRUCIBLE_FIELD_MAP.starts == "start_dates"
    assert CRUCIBLE_FIELD_MAP.categories == "department"
    assert CRUCIBLE_FIELD_MAP.items is None  # the blob *is* the array


# --------------------------------------------------------------------------- wiring


def test_parse_is_the_registry_entry_point():
    """``sources.yaml``'s ``adapter: embedded_json`` resolves to ``parse``."""
    assert parse is parse_embedded_json


def test_the_dispatch_table_wires_the_adapter_with_needs_source():
    entry = ADAPTERS["embedded_json"]

    assert entry.implemented
    assert entry.parse is parse_embedded_json
    assert entry.needs_source is True
    # It reads a blob out of one page; it never makes a second request.
    assert entry.paginates is False
    assert "embedded_json" in implemented_adapters()


def test_the_registered_crucible_source_is_runnable_now(crucible_ref: SourceRef):
    assert crucible_ref.source.script_id == SCRIPT_ID
    assert is_runnable(crucible_ref)


def test_fetch_embedded_json_drives_the_whole_flow_in_one_call(
    registry: Registry, crucible_ref: SourceRef, tmp_path: Path
):
    router = Router({"/course-search/": html_response(load("crucible-course-search.html"))})
    fetcher = make_fetcher(registry, router, tmp_path)

    result = fetch_embedded_json(fetcher, crucible_ref, today=TODAY, now=NOW, horizon_days=120)
    fetcher.close()

    assert result.ok
    assert result.event_count == 9
    # One request, and no query parameter: ?department= does nothing server-side
    # and a bogus ?department=ZZZ returns the identical blob.
    assert len(router.urls) == 1
    assert router.urls[0] == CRUCIBLE_URL
    assert "?" not in router.urls[0]


def test_process_source_reads_the_script_id_out_of_the_registry(
    registry: Registry, crucible_ref: SourceRef, tmp_path: Path
):
    """The CLI seam: the adapter needs the ``SourceRef`` and not the ``Fetcher``."""
    router = Router({"/course-search/": html_response(load("crucible-course-search.html"))})
    fetcher = make_fetcher(registry, router, tmp_path)

    record, events = process_source(crucible_ref, fetcher, horizon_days=120, now=NOW)
    fetcher.close()

    assert record.status == "ok"
    assert record.problem == "none"
    assert record.raw_count == 13  # offerings
    assert record.horizon_count == 9  # the health-gate number
    assert record.event_count == 9
    assert len(events) == 9
    assert all(event.tz == DEFAULT_ZONE for event in events)
    assert all(event.address == "1260 7th St, Oakland, CA 94607" for event in events)


def test_a_drifted_blob_fails_the_source_rather_than_emptying_it(
    registry: Registry, crucible_ref: SourceRef, tmp_path: Path
):
    router = Router({"/course-search/": html_response(load("crucible-no-blob.html"))})
    fetcher = make_fetcher(registry, router, tmp_path)

    record, events = process_source(crucible_ref, fetcher, horizon_days=120, now=NOW)
    fetcher.close()

    assert record.status == "failed"
    assert record.problem == "script_not_found"
    assert events == []
