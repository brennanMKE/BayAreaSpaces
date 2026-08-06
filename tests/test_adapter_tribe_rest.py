"""Tests for the Tribe REST adapter (issue 0019).

**No test here touches the network.** Issue 0001 — the bot about page that
``$MAKER_CALENDAR_CONTACT`` points at — is still open, so a live request would
go out under a User-Agent naming a page that does not resolve. Every request in
this file goes through ``httpx.MockTransport``, and every payload is a
hand-authored fixture under ``tests/fixtures/tribe/``.

``today`` is pinned to **2026-08-05**, the source-survey date, so the horizon
counts below are exact rather than approximate.

Four things are being defended.

**Pagination is the feed's own link.** Ace publishes 92 upcoming events 50 to a
page, so page 1 alone is a healthy-looking 54% of the calendar. The adapter
follows ``next_rest_url`` verbatim rather than constructing ``?page=2``, stops
on an absent link *and* on a 200 with an empty list, and refuses to keep walking
a server that always offers another page.

**``cost`` is source text.** ``&#036;20.00`` unescapes to ``$20.00``; "sliding
scale $10-30" and "free for members" survive verbatim. Parsing either to a
float would turn a range into a wrong number and a sentence into ``None``.

**Both Ace sources reach ``normalize.py`` identically.** ``TribeEvent``
subclasses ``IcsEvent``, so ``from_ics_event`` consumes REST and ICS through one
function and the two cannot drift apart.

**A 404 is a reported failure, not an empty calendar.** On this route it almost
always means The Events Calendar is not installed — The Crucible is the recorded
case, where ``tribe-*`` CSS class names are inert Avada compatibility markup and
every ``wp-json/tribe/*`` route 404s.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from pipeline.adapters.ics import IcsEvent
from pipeline.adapters.tribe_rest import (
    DEFAULT_PER_PAGE,
    MAX_PAGES,
    TribeEvent,
    TribeParse,
    TribeProblem,
    events_url,
    fetch_tribe_rest,
    looks_like_json_object,
    parse,
    parse_tribe_rest,
    parse_tribe_rest_text,
)
from pipeline.config import Registry, SourceRef, load_registry
from pipeline.fetch import FetchResult, Fetcher, Outcome
from pipeline.normalize import from_ics_event, normalize_ics

FIXTURES = Path(__file__).parent / "fixtures" / "tribe"

#: The source-survey date. Every fixture is authored relative to it.
TODAY = dt.date(2026, 8, 5)
NOW = dt.datetime(2026, 8, 5, 3, 15, tzinfo=dt.timezone.utc)
PACIFIC = ZoneInfo("America/Los_Angeles")

TEST_CONTACT = "https://maker-calendar.test/about"

BASE_URL = "https://www.acemakerspace.org/wp-json/tribe/events/v1/events"
PAGE_1_URL = f"{BASE_URL}?start_date=2026-08-05&per_page=50&page=1"
PAGE_2_URL = f"{BASE_URL}?start_date=2026-08-05&per_page=50&page=2"


# --------------------------------------------------------------------------- helpers


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fetched(
    body: str | bytes,
    *,
    content_type: str | None = "application/json",
    status_code: int = 200,
    outcome: Outcome = Outcome.FETCHED,
    label: str = "tribe-rest",
    space_id: str = "ace-makerspace",
    url: str = PAGE_1_URL,
    reason: str | None = None,
) -> FetchResult:
    """A :class:`FetchResult` as the fetch layer would hand one to the adapter."""
    payload = body.encode("utf-8") if isinstance(body, str) else body
    return FetchResult(
        space_id=space_id,
        label=label,
        adapter="tribe_rest",
        url=url,
        outcome=outcome,
        status_code=status_code,
        content_type=content_type,
        body=payload,
        byte_count=len(payload),
        charset="utf-8",
        reason=reason,
    )


def parse_page_1(**kwargs: Any) -> TribeParse:
    """Page 1 only — no fetcher, so no pagination."""
    kwargs.setdefault("today", TODAY)
    kwargs.setdefault("now", NOW)
    return parse_tribe_rest(fetched(load("ace-page-1.json")), **kwargs)


def titles(result: TribeParse) -> list[str]:
    return [event.title or "" for event in result.events]


def by_title(result: TribeParse, needle: str) -> TribeEvent:
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
        return httpx.Response(404, content=b"{}", headers={"Content-Type": "application/json"})

    @property
    def urls(self) -> list[str]:
        return [str(request.url) for request in self.requests if request.url.path != "/robots.txt"]

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def json_response(body: str, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        content=body.encode("utf-8"),
        headers={"Content-Type": "application/json; charset=UTF-8"},
    )


def noop_sleep(seconds: float) -> None:
    """Ace's robots.txt sets Crawl-delay: 10. Not in a test suite."""


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def registry() -> Registry:
    return load_registry(env={"MAKER_CALENDAR_CONTACT": TEST_CONTACT})


@pytest.fixture
def ace_ref(registry: Registry) -> SourceRef:
    space = registry.space("ace-makerspace")
    source = next(s for s in space.sources if s.adapter == "tribe_rest")
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


def test_a_single_page_parses():
    result = parse_page_1()

    assert result.ok
    assert result.problem is TribeProblem.NONE
    assert result.page_count == 1
    assert result.vevent_count == 3
    assert result.event_count == 3
    assert titles(result) == [
        "Laser Cutter Basics & Safety",
        "Textiles Open Studio",
        "Virtual New Member Orientation",
    ]


def test_parse_is_the_registry_entry_point():
    """``sources.yaml``'s ``adapter: tribe_rest`` resolves to ``parse``."""
    assert parse is parse_tribe_rest


def test_utc_start_date_becomes_an_aware_local_datetime():
    """``utc_start_date`` is the authoritative instant; the zone restores the
    wall clock the site displays."""
    event = by_title(parse_page_1(), "Laser Cutter")

    assert event.start == dt.datetime(2026, 8, 7, 1, 0, tzinfo=dt.timezone.utc)
    assert event.start.tzinfo is not None
    assert event.start.astimezone(PACIFIC).hour == 18  # start_date said 18:00
    assert event.tz == "America/Los_Angeles"
    assert event.source_tz == "America/Los_Angeles"
    assert event.dtstart_form == "tzid"
    assert event.end == dt.datetime(2026, 8, 7, 4, 0, tzinfo=dt.timezone.utc)


def test_no_naive_datetime_ever_leaves_the_adapter():
    """The invariant, asserted at the seam rather than trusted."""
    for event in parse_page_1():
        if isinstance(event.start, dt.datetime):
            assert event.start.tzinfo is not None
        if isinstance(event.end, dt.datetime):
            assert event.end.tzinfo is not None


def test_the_events_are_sorted_by_start():
    starts = [event.start for event in parse_page_1()]
    assert starts == sorted(starts)


# --------------------------------------------------------------------------- cost


def test_escaped_cost_is_unescaped():
    """``&#036;20.00`` is what Ace actually returns."""
    assert by_title(parse_page_1(), "Laser Cutter").cost == "$20.00"


def test_a_non_numeric_cost_survives_verbatim():
    """"sliding scale" and "free for members" are real values in this dataset.

    Parsing ``cost`` to a float would turn the first into a wrong number and the
    second into ``None``, and nothing downstream would ever say so.
    """
    assert by_title(parse_page_1(), "Textiles").cost == "sliding scale $10-30"
    assert by_title(parse_page_1(), "Orientation").cost == "free for members"


def test_cost_is_never_parsed_to_a_number():
    for event in parse_page_1():
        assert event.cost is None or isinstance(event.cost, str)


def test_cost_reaches_the_canonical_schema_as_price(registry: Registry):
    """The whole point of the unescaping: ``Event.price`` is source text."""
    space = registry.space("ace-makerspace")
    normalization = normalize_ics(
        parse_page_1(), space=space, source_label="tribe-rest", now=NOW
    )
    prices = {event.title: event.price for event in normalization.events}

    assert prices["Laser Cutter Basics & Safety"] == "$20.00"
    assert prices["Textiles Open Studio"] == "sliding scale $10-30"


def test_an_empty_cost_string_is_none():
    """``"cost": ""`` is TEC's "no price set", not a price of nothing."""
    page_2 = parse_tribe_rest(
        fetched(load("ace-page-2.json"), url=PAGE_2_URL), today=TODAY, now=NOW
    )
    assert by_title(page_2, "First Friday").cost is None


# --------------------------------------------------------------------------- fields


def test_venue_maps_through():
    event = by_title(parse_page_1(), "Laser Cutter")

    assert event.venue_name == "Ace Makerspace Suite 214"
    assert event.venue_address == "6050 Lowell Street, Suite #214, Oakland, CA, 94608, United States"
    # normalize.py reads `location`, exactly as it does for the ICS source.
    assert event.location is not None
    assert event.location.startswith("Ace Makerspace Suite 214, 6050 Lowell Street")


def test_organizer_maps_through():
    event = by_title(parse_page_1(), "Laser Cutter")

    assert event.organizers == ("Laser Team",)
    # IcsEvent.organizer carries the first, for parity with ICS ORGANIZER;CN=.
    assert event.organizer == "Laser Team"


def test_an_event_with_no_organizer_is_not_a_failure():
    event = by_title(parse_page_1(), "Orientation")
    assert event.organizers == ()
    assert event.organizer is None


def test_categories_map_through_as_names():
    """Issue 0010's ``categories_exclude`` runs on these, so they must arrive."""
    assert by_title(parse_page_1(), "Laser Cutter").categories == ("Laser", "Workshop")
    assert by_title(parse_page_1(), "Textiles").categories == ("Textiles",)


def test_is_virtual_and_ticket_status_map_through():
    laser = by_title(parse_page_1(), "Laser Cutter")
    virtual = by_title(parse_page_1(), "Orientation")

    assert laser.ticketed is True
    assert laser.is_virtual is False
    assert virtual.is_virtual is True
    assert virtual.ticketed is False
    assert virtual.venue_name == "Ace Virtual Event"
    assert virtual.venue_address is None


def test_titles_and_status_are_unescaped_and_mapped():
    event = by_title(parse_page_1(), "Laser Cutter")

    assert event.title == "Laser Cutter Basics & Safety"  # &#038; in the source
    assert event.status == "CONFIRMED"  # WordPress "publish"
    assert event.url == (
        "https://www.acemakerspace.org/event/laser-cutter-basics-2026-08-06/"
    )
    assert event.event_id == 40101
    assert event.uid == "www.acemakerspace.org?id=40101"
    assert event.page == 1


def test_website_is_kept_apart_from_the_permalink():
    """TEC's ``website`` is an organizer link, not the event page."""
    event = by_title(parse_page_1(), "Textiles")
    assert event.website == "https://www.acemakerspace.org/textiles/"
    assert event.url.endswith("/event/textiles-open-studio-2026-08-07/")


def test_staleness_uses_the_newest_modified_utc():
    """Issue 0016's ``max_stale_days`` gate runs on this."""
    result = parse_page_1()

    assert result.last_modified == dt.datetime(2026, 8, 1, 9, 2, 11, tzinfo=dt.timezone.utc)
    assert result.last_change == result.last_modified
    assert result.stale_days is not None
    assert 3 < result.stale_days < 5


# --------------------------------------------------------------------------- shape


def test_a_tribe_event_is_an_ics_event():
    """One intermediate shape, so ``normalize.py`` consumes both identically."""
    event = by_title(parse_page_1(), "Laser Cutter")
    assert isinstance(event, IcsEvent)

    raw = from_ics_event(event)
    assert raw.source_uid == event.uid
    assert raw.start == event.start
    assert raw.categories == event.categories
    assert raw.price == "$20.00"


def test_the_parse_result_is_frozen():
    result = parse_page_1()
    with pytest.raises(Exception):
        result.events = ()  # type: ignore[misc]
    with pytest.raises(Exception):
        result.events[0].title = "no"  # type: ignore[misc]


def test_normalize_consumes_the_parse_unchanged(registry: Registry):
    space = registry.space("ace-makerspace")
    normalization = normalize_ics(
        parse_page_1(), space=space, source_label="tribe-rest", now=NOW
    )

    assert normalization.event_count == 3
    for event in normalization.events:
        assert event.start_utc.tzinfo is dt.timezone.utc
        assert event.uid.startswith("ace-makerspace:")


# --------------------------------------------------------------------------- horizon


def test_events_beyond_the_horizon_are_clipped_but_still_counted():
    """TEC defaults ``end_date`` to +2 years; the horizon is 120 days."""
    result = parse_tribe_rest(
        fetched(load("ace-page-2.json"), url=PAGE_2_URL), today=TODAY, now=NOW
    )

    assert result.ok
    assert result.vevent_count == 2
    assert result.event_count == 1
    assert "Annual Open House" not in titles(result)
    assert result.window_end == TODAY + dt.timedelta(days=120)


def test_an_all_day_event_keeps_an_inclusive_end():
    """TEC already writes the inclusive last day — no RFC 5545 correction."""
    result = parse_tribe_rest(
        fetched(load("ace-page-2.json"), url=PAGE_2_URL), today=TODAY, now=NOW
    )
    event = by_title(result, "First Friday")

    assert event.all_day is True
    assert event.start == dt.date(2026, 9, 4)
    assert event.end == dt.date(2026, 9, 5)
    assert event.multi_day is True
    assert event.days == 2
    assert event.dtstart_form == "date"


# --------------------------------------------------------------------------- pagination


def test_two_pages_paginate_on_next_rest_url_and_concatenate(
    registry: Registry, ace_ref: SourceRef, tmp_path: Path
):
    router = Router(
        {
            "page=2": json_response(load("ace-page-2.json")),
            BASE_URL: json_response(load("ace-page-1.json")),
        }
    )
    fetcher = make_fetcher(registry, router, tmp_path)

    result = parse_tribe_rest(
        fetched(load("ace-page-1.json")),
        today=TODAY,
        now=NOW,
        fetcher=fetcher,
        ref=ace_ref,
    )
    fetcher.close()

    assert result.ok
    assert result.page_count == 2
    assert result.vevent_count == 5  # 3 + 2, before horizon clipping
    assert result.event_count == 4  # one event is past the horizon
    assert result.truncated is False
    # The feed's own link was followed, not a constructed ?page=2.
    assert router.urls == [PAGE_2_URL]
    assert [event.page for event in result.events if event.page == 2]
    assert result.reported_total == 5
    assert result.reported_pages == 2
    assert result.counts_agree


def test_page_two_is_archived_under_its_own_name(
    registry: Registry, ace_ref: SourceRef, tmp_path: Path
):
    """``raw/`` is the evidence trail, and page 2 is evidence too."""
    router = Router({"page=2": json_response(load("ace-page-2.json"))})
    fetcher = make_fetcher(registry, router, tmp_path)

    parse_tribe_rest(
        fetched(load("ace-page-1.json")),
        today=TODAY,
        now=NOW,
        fetcher=fetcher,
        ref=ace_ref,
    )
    fetcher.close()

    written = sorted(path.name for path in (tmp_path / "raw").rglob("*.json"))
    assert written == ["ace-makerspace-tribe-rest-p2.json"]


def test_pagination_stops_on_an_absent_next_rest_url(
    registry: Registry, ace_ref: SourceRef, tmp_path: Path
):
    """Page 2 of 2 has no ``next_rest_url``. That is the ordinary end."""
    router = Router({"page=2": json_response(load("ace-page-2.json"))})
    fetcher = make_fetcher(registry, router, tmp_path)

    result = parse_tribe_rest(
        fetched(load("ace-page-1.json")),
        today=TODAY,
        now=NOW,
        fetcher=fetcher,
        ref=ace_ref,
    )
    fetcher.close()

    assert result.ok
    assert result.page_count == 2
    assert len(router.urls) == 1


def test_pagination_stops_on_an_empty_list_without_erroring(
    registry: Registry, ace_ref: SourceRef, tmp_path: Path
):
    """A 200 with ``"events": []`` is the end of the feed, not a failure."""
    page_1 = json.loads(load("ace-page-1.json"))
    page_2 = json.loads(load("ace-page-2.json"))
    page_2["next_rest_url"] = f"{BASE_URL}?start_date=2026-08-05&per_page=50&page=3"

    router = Router(
        {
            "page=3": json_response(load("empty-page.json")),
            "page=2": json_response(json.dumps(page_2)),
        }
    )
    fetcher = make_fetcher(registry, router, tmp_path)

    result = parse_tribe_rest(
        fetched(json.dumps(page_1)), today=TODAY, now=NOW, fetcher=fetcher, ref=ace_ref
    )
    fetcher.close()

    assert result.ok
    assert result.problem is TribeProblem.NONE
    assert result.page_count == 3
    assert result.vevent_count == 5
    assert len(router.urls) == 2


def test_a_runaway_next_rest_url_loop_is_capped(
    registry: Registry, ace_ref: SourceRef, tmp_path: Path
):
    """A server that always offers another page must not loop the run."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        page = int(request.url.params.get("page", "1"))
        body = json.loads(load("ace-page-1.json"))
        body["next_rest_url"] = f"{BASE_URL}?start_date=2026-08-05&per_page=50&page={page + 1}"
        return json_response(json.dumps(body))

    requests: list[httpx.Request] = []

    def counting(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/robots.txt":
            requests.append(request)
        return handler(request)

    fetcher = Fetcher(
        registry,
        transport=httpx.MockTransport(counting),
        raw_dir=tmp_path / "raw",
        sleep=noop_sleep,
        clock=FakeClock(),
    )
    result = parse_tribe_rest(
        fetched(load("ace-page-1.json")),
        today=TODAY,
        now=NOW,
        fetcher=fetcher,
        ref=ace_ref,
        max_pages=4,
    )
    fetcher.close()

    assert not result.ok
    assert result.problem is TribeProblem.RUNAWAY_PAGINATION
    assert "after 4 pages" in (result.error or "")
    assert result.page_count == 4
    assert len(requests) == 3  # pages 2, 3, 4; page 1 came from the FetchResult


def test_the_default_page_cap_is_generous_but_finite():
    """1000 events at 50 a page, against a largest observed feed of 92."""
    assert MAX_PAGES == 20
    assert DEFAULT_PER_PAGE == 50


def test_a_pagination_loop_back_to_a_seen_url_is_reported(
    registry: Registry, ace_ref: SourceRef, tmp_path: Path
):
    page_2 = json.loads(load("ace-page-2.json"))
    page_2["next_rest_url"] = PAGE_2_URL  # points back at itself

    router = Router({"page=2": json_response(json.dumps(page_2))})
    fetcher = make_fetcher(registry, router, tmp_path)

    result = parse_tribe_rest(
        fetched(load("ace-page-1.json")),
        today=TODAY,
        now=NOW,
        fetcher=fetcher,
        ref=ace_ref,
    )
    fetcher.close()

    assert not result.ok
    assert result.problem is TribeProblem.RUNAWAY_PAGINATION
    assert "pagination loop" in (result.error or "")


def test_a_failure_on_page_two_fails_the_whole_parse(
    registry: Registry, ace_ref: SourceRef, tmp_path: Path
):
    """Half a calendar that looks healthy is worse than carrying forward."""
    router = Router({"page=2": httpx.Response(404, content=b"nope")})
    fetcher = make_fetcher(registry, router, tmp_path)

    result = parse_tribe_rest(
        fetched(load("ace-page-1.json")),
        today=TODAY,
        now=NOW,
        fetcher=fetcher,
        ref=ace_ref,
    )
    fetcher.close()

    assert not result.ok
    assert result.problem is TribeProblem.PAGE_FAILED
    assert "page 2" in (result.error or "")
    assert "deliberately not published" in (result.error or "")


def test_a_single_page_call_reports_that_it_did_not_paginate():
    """No fetcher means one page, said out loud rather than silently."""
    result = parse_page_1()

    assert result.ok
    assert result.truncated is True
    assert "next_rest_url present" in (result.error or "")


def test_parse_text_reads_one_page_and_says_so():
    result = parse_tribe_rest_text(
        load("ace-page-1.json"),
        space_id="ace-makerspace",
        label="tribe-rest",
        source_url=PAGE_1_URL,
        today=TODAY,
        now=NOW,
    )

    assert result.ok
    assert result.event_count == 3
    assert result.truncated is True
    assert "one page only" in (result.error or "")


# --------------------------------------------------------------------------- failures


def test_a_404_is_a_clear_reported_failure():
    """The Crucible's case, and it must never look like an empty calendar."""
    result = parse_tribe_rest(
        fetched(load("rest-no-route.json"), status_code=404),
        today=TODAY,
        now=NOW,
    )

    assert not result.ok
    assert result.problem is TribeProblem.HTTP_ERROR
    assert result.event_count == 0
    error = result.error or ""
    assert "HTTP 404" in error
    assert "not installed" in error
    assert "Crucible" in error
    assert "Avada" in error


def test_a_200_with_html_instead_of_json_is_reported_not_crashed():
    result = parse_tribe_rest(
        fetched(load("rendered-page.html"), content_type="text/html"),
        today=TODAY,
        now=NOW,
    )

    assert not result.ok
    assert result.problem is TribeProblem.WRONG_CONTENT_TYPE
    assert "HTTP 200 is not success" in (result.error or "")
    assert result.event_count == 0


def test_html_wearing_a_json_content_type_is_still_caught():
    """The header lies sometimes; the body is sniffed either way."""
    result = parse_tribe_rest(
        fetched(load("rendered-page.html"), content_type="application/json"),
        today=TODAY,
        now=NOW,
    )

    assert not result.ok
    assert result.problem is TribeProblem.NOT_JSON
    assert "not JSON" in (result.error or "")


def test_valid_json_that_is_not_a_tribe_document_is_reported():
    """``rest_no_route`` with a 200 — an adapter returning [] would be silent."""
    result = parse_tribe_rest(load_result := fetched(load("rest-no-route.json")))

    assert load_result.status_code == 200
    assert not result.ok
    assert result.problem is TribeProblem.NOT_TRIBE
    assert "rest_no_route" in (result.error or "")


def test_a_json_array_is_not_a_tribe_document():
    result = parse_tribe_rest(fetched("[]"), today=TODAY, now=NOW)

    assert not result.ok
    assert result.problem is TribeProblem.NOT_TRIBE


def test_an_empty_body_is_reported():
    result = parse_tribe_rest(fetched(b""), today=TODAY, now=NOW)

    assert not result.ok
    assert result.problem is TribeProblem.EMPTY_BODY


def test_a_304_is_not_zero_events():
    result = parse_tribe_rest(
        fetched(b"", outcome=Outcome.NOT_MODIFIED, status_code=304),
        today=TODAY,
        now=NOW,
    )

    assert not result.ok
    assert result.problem is TribeProblem.NOT_MODIFIED
    assert "reuse the stored events" in (result.error or "")


def test_a_transport_failure_is_reported_as_transport():
    result = parse_tribe_rest(
        fetched(b"", outcome=Outcome.FAILED, status_code=None, reason="connection died"),
        today=TODAY,
        now=NOW,
    )

    assert not result.ok
    assert result.problem is TribeProblem.TRANSPORT
    assert "connection died" in (result.error or "")


def test_a_blocked_source_is_reported_as_transport():
    result = parse_tribe_rest(
        fetched(b"", outcome=Outcome.BLOCKED, status_code=None, reason="robots.txt"),
        today=TODAY,
        now=NOW,
    )

    assert not result.ok
    assert result.problem is TribeProblem.TRANSPORT


def test_json_served_as_text_plain_is_tolerated():
    """Sloppy, not dishonest — the body really is a TEC document."""
    result = parse_tribe_rest(
        fetched(load("ace-page-1.json"), content_type="text/plain"),
        today=TODAY,
        now=NOW,
    )

    assert result.ok
    assert result.event_count == 3
    assert "tolerated" in (result.error or "")


def test_a_failed_parse_normalizes_to_nothing_rather_than_raising(registry: Registry):
    space = registry.space("ace-makerspace")
    result = parse_tribe_rest(
        fetched(load("rendered-page.html"), content_type="text/html"),
        today=TODAY,
        now=NOW,
    )
    normalization = normalize_ics(result, space=space, source_label="tribe-rest", now=NOW)

    assert normalization.event_count == 0


def test_looks_like_json_object():
    assert looks_like_json_object('{"events": []}')
    assert looks_like_json_object('﻿  {"events": []}')
    assert not looks_like_json_object("<!DOCTYPE html>")
    assert not looks_like_json_object("[]")


# --------------------------------------------------------------------------- the URL


def test_events_url_fills_in_what_the_registry_left_out():
    url = events_url(BASE_URL, start_date=TODAY, per_page=50)

    assert url.params["start_date"] == "2026-08-05"
    assert url.params["per_page"] == "50"
    assert str(url).startswith(BASE_URL)


def test_events_url_never_overrides_the_registry():
    """``sources.yaml`` is authoritative; this only supplies what is missing."""
    url = events_url(f"{BASE_URL}?per_page=10&start_date=2026-01-01", start_date=TODAY)

    assert url.params["per_page"] == "10"
    assert url.params["start_date"] == "2026-01-01"


# --------------------------------------------------------------------------- fetching


def test_fetch_tribe_rest_walks_every_page(
    registry: Registry, ace_ref: SourceRef, tmp_path: Path
):
    router = Router(
        {
            "page=2": json_response(load("ace-page-2.json")),
            "/wp-json/tribe/events/v1/events": json_response(load("ace-page-1.json")),
        }
    )
    fetcher = make_fetcher(registry, router, tmp_path)

    result = fetch_tribe_rest(fetcher, ace_ref, today=TODAY, now=NOW)
    fetcher.close()

    assert result.ok
    assert result.page_count == 2
    assert result.event_count == 4
    # start_date and per_page were supplied on the first request.
    assert "start_date=2026-08-05" in router.urls[0]
    assert "per_page=50" in router.urls[0]
    assert router.urls[1] == PAGE_2_URL


def test_the_nightly_run_paginates_through_the_dispatch_table(
    registry: Registry, ace_ref: SourceRef, tmp_path: Path
):
    """The CLI seam: ``paginates=True`` is what hands the adapter the fetcher.

    Without it ``process_source`` would parse page 1 and stop, and Ace would
    publish 50 of its 92 events every night without anything erroring.
    """
    from pipeline.cli import ADAPTERS, process_source

    assert ADAPTERS["tribe_rest"].paginates is True

    router = Router(
        {
            "page=2": json_response(load("ace-page-2.json")),
            "/wp-json/tribe/events/v1/events": json_response(load("ace-page-1.json")),
        }
    )
    fetcher = make_fetcher(registry, router, tmp_path)

    record, events = process_source(ace_ref, fetcher, horizon_days=120, now=NOW)
    fetcher.close()

    assert record.status == "ok"
    assert record.raw_count == 5  # both pages, before horizon clipping
    assert record.horizon_count == 4
    assert len(events) == 4
    assert len(router.urls) == 2
    assert {event.price for event in events} >= {"$20.00", "sliding scale $10-30"}


def test_fetch_tribe_rest_reports_a_404_without_raising(
    registry: Registry, ace_ref: SourceRef, tmp_path: Path
):
    router = Router({"/wp-json/tribe/": json_response(load("rest-no-route.json"), 404)})
    fetcher = make_fetcher(registry, router, tmp_path)

    result = fetch_tribe_rest(fetcher, ace_ref, today=TODAY, now=NOW)
    fetcher.close()

    assert not result.ok
    assert result.problem is TribeProblem.HTTP_ERROR
