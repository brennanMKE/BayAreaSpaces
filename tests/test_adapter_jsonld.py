"""Tests for the JSON-LD adapter (issue 0020).

**No test here touches the network.** Issue 0001 — the bot about page that
``$MAKER_CALENDAR_CONTACT`` points at — is still open, so a live request would
go out under a User-Agent naming a page that does not resolve. Every request in
this file goes through ``httpx.MockTransport`` and every payload is a
hand-authored fixture under ``tests/fixtures/jsonld/``.

``today`` is pinned to **2026-08-05**, the source-survey date, so the horizon
counts below are exact rather than approximate.

Five things are being defended.

**Every ld+json block is collected, not the first.** Ace's ``/calendar/`` puts a
``WebSite`` block in front of its events. And one malformed block must not cost
the good ones: ``extruct`` *raises* on invalid JSON, so a single ``extract()``
over a document with four good blocks and one truncated one returns nothing at
all.

**All four real shapes parse.** ``@graph`` nesting, a bare array,
``ItemList``/``ListItem`` wrapping, and ``"@type": ["Event", "EducationEvent"]``
— a list, and a subtype — which is what Eventbrite event pages emit.

**Offsets survive.** ``2026-08-08T18:00:00-0700`` keeps ``utcoffset() == -7h``
and stays that exact instant.

**Zero events is not one thing.** A page whose only structured data is an
undated ``schema.org/Course`` (The Crucible) and a page with no ld+json at all
(Eventbrite organizer pages) are reported failures that say what they found. A
seed feed with no items (The Box Shop between events) is ``ok`` with zero.

**The Box Shop's two-step flow works end to end.** RSS for the links, per-event
page for the dates, one rate limiter and one ``raw/`` archive throughout, a cap
on how many pages will be read, and a 404 on one seed that does not take the
source down with it.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import httpx
import pytest

from pipeline.adapters.ics import IcsEvent
from pipeline.adapters.jsonld import (
    DEFAULT_ZONE,
    DISALLOWED_QUERY_FORMATS,
    MAX_SEED_FETCHES,
    JsonLdEvent,
    JsonLdParse,
    JsonLdProblem,
    collect_types,
    fetch_jsonld,
    is_disallowed_route,
    is_event_type,
    iter_events,
    ld_blocks,
    ld_items,
    looks_like_feed,
    parse,
    parse_jsonld,
    parse_jsonld_text,
    parse_ld_datetime,
    seed_links,
)
from pipeline.config import Registry, SourceRef, load_registry
from pipeline.fetch import FetchResult, Fetcher, Outcome
from pipeline.normalize import from_ics_event, normalize_ics

FIXTURES = Path(__file__).parent / "fixtures" / "jsonld"

#: The source-survey date. Every fixture is authored relative to it.
TODAY = dt.date(2026, 8, 5)
NOW = dt.datetime(2026, 8, 5, 3, 15, tzinfo=dt.timezone.utc)
PACIFIC_SUMMER = dt.timezone(dt.timedelta(hours=-7))

TEST_CONTACT = "https://maker-calendar.test/about"

ACE_URL = "https://www.acemakerspace.org/calendar/"
BOX_SEED_URL = "https://boxshopsf.org/events?format=rss"
BOX_EVENT_URL = "https://boxshopsf.org/events/flg-heavy-pettng-zoo-benefit"
BOX_EVENT_2_URL = "https://boxshopsf.org/events/open-studios-preview"


# --------------------------------------------------------------------------- helpers


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fetched(
    body: str | bytes,
    *,
    content_type: str | None = "text/html",
    status_code: int = 200,
    outcome: Outcome = Outcome.FETCHED,
    label: str = "calendar-jsonld",
    space_id: str = "ace-makerspace",
    url: str = ACE_URL,
    reason: str | None = None,
) -> FetchResult:
    """A :class:`FetchResult` as the fetch layer would hand one to the adapter."""
    payload = body.encode("utf-8") if isinstance(body, str) else body
    return FetchResult(
        space_id=space_id,
        label=label,
        adapter="jsonld",
        url=url,
        outcome=outcome,
        status_code=status_code,
        content_type=content_type,
        body=payload,
        byte_count=len(payload),
        charset="utf-8",
        reason=reason,
    )


def seed_result(body: str | None = None, **kwargs: Any) -> FetchResult:
    """The Box Shop's ``?format=rss`` seed feed, as fetched."""
    kwargs.setdefault("content_type", "application/rss+xml")
    kwargs.setdefault("label", "squarespace-events")
    kwargs.setdefault("space_id", "the-box-shop")
    kwargs.setdefault("url", BOX_SEED_URL)
    return fetched(body if body is not None else load("boxshop-events.rss"), **kwargs)


def parse_ace(**kwargs: Any) -> JsonLdParse:
    kwargs.setdefault("today", TODAY)
    kwargs.setdefault("now", NOW)
    return parse_jsonld(fetched(load("ace-calendar.html")), **kwargs)


def titles(result: JsonLdParse) -> list[str]:
    return [event.title or "" for event in result.events]


def by_title(result: JsonLdParse, needle: str) -> JsonLdEvent:
    for event in result.events:
        if event.title and needle in event.title:
            return event  # type: ignore[return-value]
    raise AssertionError(f"no event titled {needle!r} in {titles(result)}")


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
        return httpx.Response(404, content=b"<html>not found</html>",
                              headers={"Content-Type": "text/html"})

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


def rss_response(body: str, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        content=body.encode("utf-8"),
        headers={"Content-Type": "application/rss+xml; charset=UTF-8"},
    )


def noop_sleep(seconds: float) -> None:
    """2 s per host, 10 s for Ace. Not in a test suite."""


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def registry() -> Registry:
    return load_registry(env={"MAKER_CALENDAR_CONTACT": TEST_CONTACT})


@pytest.fixture
def box_ref(registry: Registry) -> SourceRef:
    space = registry.space("the-box-shop")
    source = next(s for s in space.sources if s.adapter == "jsonld")
    return SourceRef(space, source)


@pytest.fixture
def ace_ref(registry: Registry) -> SourceRef:
    space = registry.space("ace-makerspace")
    source = next(s for s in space.sources if s.adapter == "jsonld")
    return SourceRef(space, source)


def make_fetcher(registry: Registry, router: Router, tmp_path: Path) -> Fetcher:
    return Fetcher(
        registry,
        transport=router.transport(),
        raw_dir=tmp_path / "raw",
        sleep=noop_sleep,
        clock=FakeClock(),
    )


# --------------------------------------------------------------------------- one page


def test_the_registered_ace_page_parses():
    result = parse_ace()

    assert result.ok
    assert result.problem is JsonLdProblem.NONE
    assert result.page_count == 1
    assert result.vevent_count == 3  # before horizon clipping
    assert result.event_count == 2  # the 2027 open house is past the horizon
    assert titles(result) == ["Laser Cutter Basics & Safety", "Textiles Open Studio"]


def test_parse_is_the_registry_entry_point():
    """``sources.yaml``'s ``adapter: jsonld`` resolves to ``parse``."""
    assert parse is parse_jsonld


def test_every_ld_json_block_is_collected_not_just_the_first():
    """Ace puts a ``WebSite`` block in front of the events."""
    blocks = ld_blocks(load("ace-calendar.html"))
    assert len(blocks) == 2
    assert "WebSite" in blocks[0]
    assert "Laser Cutter" in blocks[1]

    result = parse_ace()
    assert result.block_count == 2
    assert result.pages[0].item_count == 4  # WebSite + 3 flattened array entries


def test_a_malformed_block_does_not_cost_the_good_ones():
    """``extruct`` raises on invalid JSON, so blocks are decoded one at a time.

    A single ``extruct.extract()`` over ``shapes.html`` returns **nothing** —
    the truncated fifth block takes the four good ones with it.
    """
    import extruct

    with pytest.raises(ValueError):
        extruct.extract(load("shapes.html"), syntaxes=["json-ld"])

    items, blocks, bad = ld_items(load("shapes.html"))
    assert blocks == 5
    assert bad == 1
    assert len(items) == 5  # 2 flattened from the array + @graph + ItemList + typed list

    result = parse_jsonld_text(load("shapes.html"), today=TODAY, now=NOW)
    assert result.ok
    assert result.bad_block_count == 1
    assert "not valid JSON" in (result.error or "")


# --------------------------------------------------------------------------- shapes


def test_all_four_json_ld_shapes_are_walked():
    """A bare array, ``@graph``, ``ItemList``/``ListItem``, and a ``@type`` list."""
    result = parse_jsonld_text(load("shapes.html"), today=TODAY, now=NOW)

    assert result.ok
    assert titles(result) == [
        "Bare Array One",
        "Bare Array Two",
        "Graph Event",
        "List Item Class",
        "List Item Social",
        "Typed As A List",
    ]


def test_a_bare_array_is_flattened():
    result = parse_jsonld_text(load("shapes.html"), today=TODAY, now=NOW)
    assert by_title(result, "Bare Array One").start.date() == dt.date(2026, 8, 10)


def test_graph_nesting_is_walked():
    result = parse_jsonld_text(load("shapes.html"), today=TODAY, now=NOW)
    event = by_title(result, "Graph Event")
    assert event.start == dt.datetime(2026, 8, 12, 19, 30, tzinfo=PACIFIC_SUMMER)


def test_item_list_and_list_item_wrappers_are_unwrapped():
    result = parse_jsonld_text(load("shapes.html"), today=TODAY, now=NOW)
    assert by_title(result, "List Item Class").ld_type == "EducationEvent"
    assert by_title(result, "List Item Social").ld_type == "Event"


def test_a_type_list_is_accepted():
    """Eventbrite event pages emit ``["Event", "EducationEvent"]``."""
    result = parse_jsonld_text(load("shapes.html"), today=TODAY, now=NOW)
    assert by_title(result, "Typed As A List").ld_type == "Event+EducationEvent"


def test_education_event_and_other_subtypes_count_as_events():
    assert is_event_type({"@type": "EducationEvent"})
    assert is_event_type({"@type": ["Event", "EducationEvent"]})
    assert is_event_type({"@type": "MusicEvent"})
    assert is_event_type({"@type": "Festival"})
    assert is_event_type({"@type": "CourseInstance"})
    assert is_event_type({"@type": "https://schema.org/SocialEvent"})
    assert not is_event_type({"@type": "Course"})
    assert not is_event_type({"@type": "Product"})
    assert not is_event_type({"name": "no type at all"})


def test_an_event_nested_inside_another_event_is_not_counted_twice():
    """The walk stops at an event, so a ``location``'s own ``event`` is ignored."""
    result = parse_jsonld_text(load("shapes.html"), today=TODAY, now=NOW)
    assert "Nested Under Location" not in titles(result)
    assert len(result.events) == 6


def test_iter_events_is_reusable_on_decoded_items():
    items, _blocks, _bad = ld_items(load("ace-calendar.html"))
    found = [node.get("name") for node in iter_events(items)]
    assert found == [
        "Laser Cutter Basics &amp; Safety",
        "Textiles Open Studio",
        "Annual Open House",
    ]


# --------------------------------------------------------------------------- dates


def test_a_minus_0700_offset_keeps_its_offset():
    """The Box Shop's confirmed value, verbatim: ``2026-08-08T18:00:00-0700``."""
    moment = parse_ld_datetime("2026-08-08T18:00:00-0700")

    assert moment.form == "offset"
    assert moment.offset == "-07:00"
    assert moment.value.utcoffset() == dt.timedelta(hours=-7)
    assert moment.value == dt.datetime(2026, 8, 8, 18, 0, tzinfo=PACIFIC_SUMMER)


def test_the_colon_form_of_the_offset_parses_identically():
    """Ace writes ``-07:00``; The Box Shop writes ``-0700``. Same instant."""
    assert (
        parse_ld_datetime("2026-08-05T18:00:00-07:00").value
        == parse_ld_datetime("2026-08-05T18:00:00-0700").value
    )


def test_the_offset_reaches_the_event_intact():
    event = by_title(parse_ace(), "Textiles")

    assert event.start == dt.datetime(2026, 8, 7, 18, 0, tzinfo=PACIFIC_SUMMER)
    assert event.start.utcoffset() == dt.timedelta(hours=-7)
    assert event.source_offset == "-07:00"
    assert event.dtstart_form == "offset"
    assert event.end == dt.datetime(2026, 8, 7, 21, 0, tzinfo=PACIFIC_SUMMER)


def test_a_timed_event_carries_a_resolvable_zone_name():
    """A numeric offset is not a timezone, and everything downstream needs one.

    ``normalize.day_start_utc`` and issue 0016's health filter both call
    ``ZoneInfo`` on ``Event.tz``, and ``ZoneInfo("UTC-07:00")`` is an error.
    """
    event = by_title(parse_ace(), "Laser Cutter")
    assert event.tz == DEFAULT_ZONE
    assert event.zone_matches_offset is True
    assert event.source_offset == "-07:00"


def test_an_offset_that_disagrees_with_the_zone_is_flagged_not_hidden():
    moment = parse_ld_datetime("2026-08-08T18:00:00+0100")
    assert moment.matches_zone is False
    assert moment.value.utcoffset() == dt.timedelta(hours=1)


def test_a_bare_date_is_an_all_day_event():
    result = parse_jsonld_text(
        load("ace-calendar.html"), today=TODAY, now=NOW, horizon_days=400
    )
    event = by_title(result, "Annual Open House")

    assert event.all_day is True
    assert event.start == dt.date(2027, 6, 12)
    assert event.end == dt.date(2027, 6, 13)
    assert event.multi_day is True
    assert event.days == 2
    assert event.dtstart_form == "date"
    # A date has no zone. normalize.py anchors it; the adapter does not guess.
    assert event.tz is None
    assert event.source_tz is None


def test_a_z_suffix_is_reported_as_utc():
    moment = parse_ld_datetime("2026-08-10T01:00:00Z")
    assert moment.form == "utc"
    assert moment.value.utcoffset() == dt.timedelta(0)


def test_an_unparseable_or_missing_date_yields_nothing_rather_than_a_crash():
    assert parse_ld_datetime("next Tuesday").value is None
    assert parse_ld_datetime(None).value is None
    assert parse_ld_datetime("").value is None


def test_no_naive_datetime_ever_leaves_the_adapter():
    """The invariant, asserted at the seam rather than trusted."""
    for event in parse_ace():
        if isinstance(event.start, dt.datetime):
            assert event.start.tzinfo is not None
        if isinstance(event.end, dt.datetime):
            assert event.end.tzinfo is not None


def test_the_events_are_sorted_by_start():
    starts = [event.start for event in parse_ace()]
    assert starts == sorted(starts)


# --------------------------------------------------------------------------- fields


def test_html_entities_in_a_block_are_unescaped():
    """Script bodies are raw text in HTML, so ``&amp;`` arrives verbatim."""
    assert by_title(parse_ace(), "Laser Cutter").title == "Laser Cutter Basics & Safety"


def test_location_maps_through_as_a_single_string():
    event = by_title(parse_ace(), "Laser Cutter")
    assert event.location == (
        "Ace Makerspace Suite 214, 6050 Lowell Street, Suite #214, Oakland, CA, 94608"
    )


def test_an_empty_location_stays_empty():
    """The Box Shop's per-event JSON-LD has ``"location": {}``.

    ``normalize.VenuePolicy`` owns ``address_override`` and applies a
    majority-based rule; filling this in here would take that decision away
    from it and erase the evidence it runs on.
    """
    result = parse_jsonld_text(
        load("boxshop-event.html"),
        space_id="the-box-shop",
        label="squarespace-events",
        today=TODAY,
        now=NOW,
    )
    assert result.ok
    assert result.events[0].location is None


def test_offers_become_source_text_and_are_never_parsed_to_a_number():
    laser = by_title(parse_ace(), "Laser Cutter")
    textiles = by_title(parse_ace(), "Textiles")

    assert laser.cost == "$20.00"
    assert laser.price == laser.cost
    assert textiles.cost == "sliding scale $10-30"
    for event in parse_ace():
        assert event.cost is None or isinstance(event.cost, str)


def test_identity_fields_map_through():
    event = by_title(parse_ace(), "Laser Cutter")

    assert event.uid == "https://www.acemakerspace.org/event/laser-cutter-basics-2026-08-06/"
    assert event.url == event.uid
    assert event.status == "CONFIRMED"
    assert event.categories == ("Laser", "Workshop")
    assert event.organizer == "Laser Team"
    assert event.page_url == ACE_URL


# --------------------------------------------------------------------------- shape


def test_a_jsonld_event_is_an_ics_event():
    """One intermediate shape, so ``normalize.py`` consumes every adapter alike."""
    event = by_title(parse_ace(), "Laser Cutter")
    assert isinstance(event, IcsEvent)

    raw = from_ics_event(event)
    assert raw.source_uid == event.uid
    assert raw.start == event.start
    assert raw.price == "$20.00"


def test_the_parse_result_is_frozen():
    result = parse_ace()
    with pytest.raises(Exception):
        result.events = ()  # type: ignore[misc]
    with pytest.raises(Exception):
        result.events[0].title = "no"  # type: ignore[misc]


def test_normalize_consumes_the_parse_unchanged(registry: Registry):
    space = registry.space("ace-makerspace")
    normalization = normalize_ics(
        parse_ace(), space=space, source_label="calendar-jsonld", now=NOW
    )

    assert normalization.event_count == 2
    for event in normalization.events:
        assert event.start_utc.tzinfo is dt.timezone.utc
        assert event.uid.startswith("ace-makerspace:")
        assert event.tz == DEFAULT_ZONE


def test_normalize_applies_the_address_override_the_adapter_left_alone(
    registry: Registry,
):
    """Issue 0009 owns this decision, and it still gets to make it."""
    space = registry.space("the-box-shop")
    parsed = parse_jsonld_text(
        load("boxshop-event.html"),
        space_id="the-box-shop",
        label="squarespace-events",
        today=TODAY,
        now=NOW,
    )
    normalization = normalize_ics(
        parsed, space=space, source_label="squarespace-events", now=NOW
    )

    assert normalization.event_count == 1
    event = normalization.events[0]
    assert event.location_name is None
    # The Box Shop has no address_override in the registry today - it is moving
    # and the recorded decision is not to publish a hardcoded address. Whatever
    # that value becomes, resolving it is normalize.py's call and not ours.
    assert event.address == (space.address_override or None)


# --------------------------------------------------------------------------- silence


def test_a_course_with_no_start_date_is_reported_not_returned_as_empty():
    """The Crucible: ``schema.org/Course``, no ``startDate``, forever.

    "A wrong adapter is as silent as a wrong URL." This must not look like a
    space with nothing on.
    """
    result = parse_jsonld(
        fetched(
            load("crucible-course.html"),
            space_id="the-crucible",
            label="course-jsonld",
            url="https://www.thecrucible.org/classes/blacksmithing/beginning-blacksmithing/",
        ),
        today=TODAY,
        now=NOW,
    )

    assert not result.ok
    assert result.problem is JsonLdProblem.NO_EVENTS
    assert result.event_count == 0
    error = result.error or ""
    assert "Course" in error
    assert "hasCourseInstance" in error
    assert "Crucible" in error
    assert "empty forever" in error
    # And it says what it *did* find, so the next person does not re-fetch it.
    assert result.types_seen == ("Course", "Organization", "Product")


def test_collect_types_reports_what_was_there_instead():
    items, _blocks, _bad = ld_items(load("crucible-course.html"))
    assert collect_types(items) == ("Course", "Organization", "Product")


def test_a_page_with_no_ld_json_at_all_is_a_reported_failure():
    """Eventbrite organizer pages stopped emitting JSON-LD (issue 0023)."""
    result = parse_jsonld(
        fetched(
            load("eventbrite-organizer.html"),
            space_id="humanmade",
            label="eventbrite-organizer",
            url="https://www.eventbrite.com/o/humanmade-57286899753",
        ),
        today=TODAY,
        now=NOW,
    )

    assert not result.ok
    assert result.problem is JsonLdProblem.NO_JSONLD
    assert result.event_count == 0
    error = result.error or ""
    assert "Eventbrite" in error
    assert "nextdata" in error
    assert "0023" in error


def test_a_200_with_the_wrong_content_type_is_reported_not_crashed():
    """``?format=ical`` answering 200 ``text/html`` has a sibling here."""
    result = parse_jsonld(
        fetched("BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n", content_type="text/calendar"),
        today=TODAY,
        now=NOW,
    )

    assert not result.ok
    assert result.problem is JsonLdProblem.WRONG_CONTENT_TYPE
    assert "HTTP 200 is not success" in (result.error or "")


def test_a_page_served_as_text_plain_is_tolerated_when_it_is_really_a_page():
    """Sloppy is not dishonest, so long as the body is a page with ld+json."""
    result = parse_jsonld(
        fetched(load("ace-calendar.html"), content_type="text/plain"),
        today=TODAY,
        now=NOW,
    )

    assert result.ok
    assert result.event_count == 2
    assert "tolerated" in (result.error or "")


def test_a_404_is_a_reported_failure():
    result = parse_jsonld(
        fetched("<html>gone</html>", status_code=404), today=TODAY, now=NOW
    )

    assert not result.ok
    assert result.problem is JsonLdProblem.HTTP_ERROR
    assert "HTTP 404" in (result.error or "")


def test_a_304_says_reuse_the_stored_events():
    result = parse_jsonld(
        fetched(b"", status_code=304, outcome=Outcome.NOT_MODIFIED), today=TODAY, now=NOW
    )

    assert not result.ok
    assert result.problem is JsonLdProblem.NOT_MODIFIED
    assert "reuse the stored events" in (result.error or "")


def test_a_transport_failure_is_reported_as_transport():
    result = parse_jsonld(
        fetched(b"", status_code=None, outcome=Outcome.FAILED, reason="timeout"),
        today=TODAY,
        now=NOW,
    )

    assert not result.ok
    assert result.problem is JsonLdProblem.TRANSPORT


# --------------------------------------------------------------------------- seeds


def test_the_seed_feed_is_recognized_and_its_links_read():
    assert looks_like_feed(load("boxshop-events.rss"))
    assert not looks_like_feed(load("ace-calendar.html"))
    assert seed_links(load("boxshop-events.rss"), base_url=BOX_SEED_URL) == (
        BOX_EVENT_URL,
        BOX_EVENT_2_URL,
    )


def test_the_two_step_box_shop_flow_end_to_end(
    registry: Registry, box_ref: SourceRef, tmp_path: Path
):
    """RSS for the links, per-event page for the dates. The confirmed value.

    The RSS ``pubDate`` is 2026-06-29 and the event is on 2026-08-08 — the whole
    reason this adapter takes a seed list instead of a feed.
    """
    router = Router(
        {
            BOX_EVENT_URL: html_response(load("boxshop-event.html")),
            BOX_EVENT_2_URL: html_response(load("boxshop-event-2.html")),
        }
    )
    fetcher = make_fetcher(registry, router, tmp_path)

    result = parse_jsonld(
        seed_result(), today=TODAY, now=NOW, fetcher=fetcher, ref=box_ref
    )
    fetcher.close()

    assert result.ok
    assert result.seed_count == 2
    assert result.followed == (BOX_EVENT_URL, BOX_EVENT_2_URL)
    assert router.urls == [BOX_EVENT_URL, BOX_EVENT_2_URL]
    assert titles(result) == ["Mutant Zoo: Feathers and Fur!", "Open Studios Preview"]

    event = by_title(result, "Mutant Zoo")
    assert event.start == dt.datetime(2026, 8, 8, 18, 0, tzinfo=PACIFIC_SUMMER)
    assert event.end == dt.datetime(2026, 8, 8, 23, 59, tzinfo=PACIFIC_SUMMER)
    assert event.page_url == BOX_EVENT_URL
    # The seed feed is a page in the record too, so `pages` accounts for every
    # request the source made.
    assert result.page_count == 3
    assert result.pages[0].is_seed is True


def test_each_per_event_page_is_archived_under_its_own_name(
    registry: Registry, box_ref: SourceRef, tmp_path: Path
):
    """``raw/`` is the evidence trail, and a follow-up page is evidence too."""
    router = Router(
        {
            BOX_EVENT_URL: html_response(load("boxshop-event.html")),
            BOX_EVENT_2_URL: html_response(load("boxshop-event-2.html")),
        }
    )
    fetcher = make_fetcher(registry, router, tmp_path)
    parse_jsonld(seed_result(), today=TODAY, now=NOW, fetcher=fetcher, ref=box_ref)
    fetcher.close()

    written = sorted(path.name for path in (tmp_path / "raw").rglob("*.html"))
    assert written == [
        "the-box-shop-squarespace-events-flg-heavy-pettng-zoo-benefit.html",
        "the-box-shop-squarespace-events-open-studios-preview.html",
    ]


def test_a_404_on_one_seed_does_not_abort_the_whole_source(
    registry: Registry, box_ref: SourceRef, tmp_path: Path
):
    """These are independent events, not pages of one list.

    ``tribe_rest`` fails the whole parse when page 2 fails, because half a
    paginated calendar looks healthy and is not. Here, one dead event page costs
    one event and says so.
    """
    router = Router({BOX_EVENT_2_URL: html_response(load("boxshop-event-2.html"))})
    fetcher = make_fetcher(registry, router, tmp_path)

    result = parse_jsonld(
        seed_result(), today=TODAY, now=NOW, fetcher=fetcher, ref=box_ref
    )
    fetcher.close()

    assert result.ok
    assert titles(result) == ["Open Studios Preview"]
    assert len(result.failed_pages) == 1
    assert "1 of 2 per-event page(s) failed" in (result.error or "")


def test_every_seed_failing_is_a_reported_failure(
    registry: Registry, box_ref: SourceRef, tmp_path: Path
):
    """A site that is down must carry forward, not publish zero."""
    router = Router({})  # everything 404s
    fetcher = make_fetcher(registry, router, tmp_path)

    result = parse_jsonld(
        seed_result(), today=TODAY, now=NOW, fetcher=fetcher, ref=box_ref
    )
    fetcher.close()

    assert not result.ok
    assert result.problem is JsonLdProblem.SEED_FETCH_FAILED
    assert "all 2 per-event page(s) failed" in (result.error or "")


def test_the_follow_up_fetch_count_is_capped(
    registry: Registry, box_ref: SourceRef, tmp_path: Path
):
    """A feed that suddenly lists hundreds of items is a source change."""
    items = "".join(
        f"<item><title>E{n}</title>"
        f"<link>https://boxshopsf.org/events/e{n}</link></item>"
        for n in range(10)
    )
    feed = (
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
        f"<title>Events</title>{items}</channel></rss>"
    )
    router = Router({"/events/e": html_response(load("boxshop-event.html"))})
    fetcher = make_fetcher(registry, router, tmp_path)

    result = parse_jsonld(
        seed_result(feed),
        today=TODAY,
        now=NOW,
        fetcher=fetcher,
        ref=box_ref,
        max_seeds=3,
    )
    fetcher.close()

    assert result.seed_count == 10
    assert len(result.followed) == 3
    assert len(router.urls) == 3
    assert result.truncated is True
    assert len(result.skipped_seeds) == 7
    assert "over the 3-fetch cap" in (result.error or "")


def test_the_default_cap_is_generous_but_finite():
    """One item observed on 2026-08-05, at 8-12 public events a year."""
    assert MAX_SEED_FETCHES == 20


def test_a_robots_disallowed_route_is_never_even_constructed(
    registry: Registry, box_ref: SourceRef, tmp_path: Path
):
    """``?format=json`` and ``?format=ical`` are disallowed for every agent.

    The fetch layer would block them; the point is that no request is built.
    """
    router = Router({BOX_EVENT_URL: html_response(load("boxshop-event.html"))})
    fetcher = make_fetcher(registry, router, tmp_path)

    result = parse_jsonld(
        seed_result(load("boxshop-events-traps.rss")),
        today=TODAY,
        now=NOW,
        fetcher=fetcher,
        ref=box_ref,
    )
    fetcher.close()

    assert result.ok
    assert result.followed == (BOX_EVENT_URL,)
    assert not any("format=ical" in url for url in router.urls)
    reasons = dict(result.skipped_seeds)
    assert reasons[f"{BOX_EVENT_URL}?format=ical"] == (
        "robots.txt disallows this ?format= route"
    )


def test_an_off_host_seed_link_is_not_followed(
    registry: Registry, box_ref: SourceRef, tmp_path: Path
):
    """Two steps is not a crawl. Step two stays on the registered site."""
    router = Router({BOX_EVENT_URL: html_response(load("boxshop-event.html"))})
    fetcher = make_fetcher(registry, router, tmp_path)

    result = parse_jsonld(
        seed_result(load("boxshop-events-traps.rss")),
        today=TODAY,
        now=NOW,
        fetcher=fetcher,
        ref=box_ref,
    )
    fetcher.close()

    assert not any("eventbrite" in url for url in router.urls)
    reasons = dict(result.skipped_seeds)
    assert "off host" in reasons[
        "https://www.eventbrite.com/e/heavy-petting-zoo-tickets-1234567890"
    ]


def test_the_disallowed_format_list_matches_the_hosts_robots_file():
    assert DISALLOWED_QUERY_FORMATS == {
        "json",
        "json-pretty",
        "ical",
        "page-context",
        "main-content",
    }
    assert is_disallowed_route("https://boxshopsf.org/events?format=ical")
    assert is_disallowed_route("https://boxshopsf.org/events?format=json")
    # ?format=rss is allowed, and it is the whole route this adapter uses.
    assert not is_disallowed_route(BOX_SEED_URL)


def test_an_empty_seed_feed_is_legitimately_empty_not_a_failure(
    registry: Registry, box_ref: SourceRef, tmp_path: Path
):
    """The Box Shop runs 8-12 events a year and carries ``allow_zero``."""
    router = Router({})
    fetcher = make_fetcher(registry, router, tmp_path)

    result = parse_jsonld(
        seed_result(load("boxshop-events-empty.rss")),
        today=TODAY,
        now=NOW,
        fetcher=fetcher,
        ref=box_ref,
    )
    fetcher.close()

    assert result.ok
    assert result.event_count == 0
    assert router.urls == []
    assert "legitimately empty" in (result.error or "")


def test_a_seed_list_with_no_fetcher_says_so_rather_than_reporting_zero():
    result = parse_jsonld(seed_result(), today=TODAY, now=NOW)

    assert result.ok
    assert result.event_count == 0
    assert result.truncated is True
    assert result.seed_count == 2
    assert "no fetcher was supplied" in (result.error or "")
    assert "seed list, not a calendar" in (result.error or "")


def test_fetch_jsonld_drives_the_whole_flow_in_one_call(
    registry: Registry, box_ref: SourceRef, tmp_path: Path
):
    """The repair-workflow entry point: one source, every page, no run."""
    router = Router(
        {
            "format=rss": rss_response(load("boxshop-events.rss")),
            BOX_EVENT_URL: html_response(load("boxshop-event.html")),
            BOX_EVENT_2_URL: html_response(load("boxshop-event-2.html")),
        }
    )
    fetcher = make_fetcher(registry, router, tmp_path)

    result = fetch_jsonld(fetcher, box_ref, today=TODAY, now=NOW, horizon_days=120)
    fetcher.close()

    assert result.ok
    assert result.event_count == 2
    assert router.urls[0].endswith("format=rss")


def test_the_ace_source_goes_through_the_one_page_flow(
    registry: Registry, ace_ref: SourceRef, tmp_path: Path
):
    """No seed list, no follow-up requests: ``/calendar/`` is one page."""
    router = Router({"/calendar/": html_response(load("ace-calendar.html"))})
    fetcher = make_fetcher(registry, router, tmp_path)

    result = fetch_jsonld(fetcher, ace_ref, today=TODAY, now=NOW, horizon_days=120)
    fetcher.close()

    assert result.ok
    assert result.event_count == 2
    assert result.seed_count == 0
    assert len(router.urls) == 1


# --------------------------------------------------------------------------- text


def test_parse_text_reads_one_document_with_no_http_anywhere_near_it():
    result = parse_jsonld_text(
        load("ace-calendar.html"),
        space_id="ace-makerspace",
        label="calendar-jsonld",
        source_url=ACE_URL,
        today=TODAY,
        now=NOW,
    )

    assert result.ok
    assert result.event_count == 2
    assert result.space_id == "ace-makerspace"


def test_parse_text_reports_a_course_page_the_same_way():
    result = parse_jsonld_text(load("crucible-course.html"), today=TODAY, now=NOW)
    assert not result.ok
    assert result.problem is JsonLdProblem.NO_EVENTS
