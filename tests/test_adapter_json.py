"""Tests for the standalone-JSON adapter (issue 0022).

**No test here touches the network.** Issue 0001 — the bot about page that
``$MAKER_CALENDAR_CONTACT`` points at — is still open, so a live request would
go out under a User-Agent naming a page that does not resolve. Every request in
this file goes through ``httpx.MockTransport``, and every payload is a
hand-authored fixture under ``tests/fixtures/json/``.

``today`` is pinned to **2026-08-05**, the source-survey date, so the horizon
counts below are exact rather than approximate.

Six things are being defended.

**The ``orderby=date`` amputation is detected, not accepted.** This is the
headline, and it is the most dangerous shape in the whole survey: without that
parameter The Crucible's Store API returns HTTP 200, valid JSON and
``X-WP-Total: 98`` instead of 353 — 72% of the catalog gone, covering only 40 of
the 216 live courses, with nothing in the response that looks wrong.
``test_the_body_without_orderby_date_looks_perfectly_healthy`` proves the body
is unimpeachable; the two tests after it prove we refuse it anyway, once from
the request and once from the response, neither check trusting the other.

**The epoch sentinel is dropped as text.** ``01/01/70 12:00 am`` parses cleanly
and ``dateutil`` puts it in **2070**, which is inside no horizon and outside no
sanity window. ``test_the_sentinel_would_otherwise_land_in_2070`` pins that
behavior so the reason the drop exists cannot quietly stop being true.

**Pagination is the header, never until-error.** ``page=99`` answers 200 with
``[]``, so a walk that stopped on failure would spin to the ceiling and call the
result healthy.

**Maker Nexus's document key is the UID.** ``2026-08-06_2314915`` already
carries the occurrence date, which is exactly what CLAUDE.md's UID rule wants.

**The ``-07:00`` offsets survive**, and ``tz`` still carries a resolvable IANA
name, because ``normalize.day_start_utc`` and issue 0016's health filter both
call ``ZoneInfo`` on it outside the per-source try/except.

**The WooCommerce categories are not published.** 60+ of them are one-off
per-product categories equal to the class title, and issue 0010's filters run on
whatever the adapter puts in ``categories``.
"""

from __future__ import annotations

import datetime as dt
import json as stdlib_json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from dateutil import parser as dateutil_parser

from pipeline.adapters.embedded_json import FieldMap
from pipeline.adapters.json_doc import (
    AMILIA_ACTIVITIES_CACHE,
    EPOCH_SENTINEL,
    SHAPES,
    WOOCOMMERCE_STORE_PRODUCTS,
    JsonEvent,
    JsonFieldMap,
    JsonParse,
    JsonProblem,
    document_items,
    fetch_json,
    missing_required_params,
    page_url,
    parse,
    parse_json,
    parse_json_text,
    parse_us_short,
    strip_tags,
    woo_price_text,
    woo_term_values,
)
from pipeline.cli import ADAPTERS, is_runnable
from pipeline.config import Registry, RegistryError, Source, SourceRef, load_registry
from pipeline.dedupe import DEFAULT_FIELD_PREFERENCES
from pipeline.fetch import FetchResult, Fetcher, Outcome, request_url
from pipeline.normalize import normalize_ics

FIXTURES = Path(__file__).parent / "fixtures" / "json"

#: The source-survey date. Every fixture is authored relative to it.
TODAY = dt.date(2026, 8, 5)
NOW = dt.datetime(2026, 8, 5, 3, 15, tzinfo=dt.timezone.utc)
PACIFIC = ZoneInfo("America/Los_Angeles")

TEST_CONTACT = "https://maker-calendar.test/about"

CRUCIBLE_BASE = "https://www.thecrucible.org/wp-json/wc/store/v1/products"
CRUCIBLE_URL = f"{CRUCIBLE_BASE}?per_page=100&orderby=date"
#: The trap, spelled out: the same endpoint, the same 200, 98 of 353 products.
CRUCIBLE_URL_NO_ORDERBY = f"{CRUCIBLE_BASE}?per_page=100"
NEXUS_URL = "https://storage.googleapis.com/makernexus_amilia_activities_cache/events.json"

#: What the live endpoint reports with ``orderby=date``, and without it.
LIVE_TOTAL = 353
AMPUTATED_TOTAL = 98
LIVE_PAGES = 4


# --------------------------------------------------------------------------- helpers


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fetched(
    body: str | bytes,
    *,
    content_type: str | None = "application/json",
    status_code: int = 200,
    outcome: Outcome = Outcome.FETCHED,
    space_id: str = "the-crucible",
    label: str = "woocommerce-store-api",
    url: str = CRUCIBLE_URL,
    headers: dict[str, str] | None = None,
    reason: str | None = None,
) -> FetchResult:
    """A :class:`FetchResult` as the fetch layer would hand one to the adapter."""
    payload = body.encode("utf-8") if isinstance(body, str) else body
    return FetchResult(
        space_id=space_id,
        label=label,
        adapter="json",
        url=url,
        outcome=outcome,
        status_code=status_code,
        content_type=content_type,
        body=payload,
        byte_count=len(payload),
        charset="utf-8",
        headers={key.lower(): value for key, value in (headers or {}).items()},
        reason=reason,
    )


def wp_headers(total: int = LIVE_TOTAL, pages: int = LIVE_PAGES) -> dict[str, str]:
    return {"X-WP-Total": str(total), "X-WP-TotalPages": str(pages)}


def nexus(body: str | None = None, **kwargs: Any) -> FetchResult:
    return fetched(
        body if body is not None else load("maker-nexus-events.json"),
        space_id="maker-nexus",
        label="amilia-community-events-cache",
        url=NEXUS_URL,
        **kwargs,
    )


def parse_nexus(**kwargs: Any) -> JsonParse:
    kwargs.setdefault("today", TODAY)
    kwargs.setdefault("now", NOW)
    kwargs.setdefault("shape", "amilia_activities_cache")
    return parse_json(nexus(), **kwargs)


def parse_crucible_page_1(**kwargs: Any) -> JsonParse:
    """Page 1 only — no fetcher, so no pagination."""
    kwargs.setdefault("today", TODAY)
    kwargs.setdefault("now", NOW)
    kwargs.setdefault("shape", "woocommerce_store_products")
    result = kwargs.pop("result", None) or fetched(
        load("crucible-products-p1.json"), headers=wp_headers()
    )
    return parse_json(result, **kwargs)


def titles(result: JsonParse) -> list[str]:
    return [event.title or "" for event in result.events]


def uids(result: JsonParse) -> list[str]:
    return [event.uid or "" for event in result.events]


def by_title(result: JsonParse, needle: str) -> JsonEvent:
    for event in result.events:
        if event.title and needle in event.title:
            return event  # type: ignore[return-value]
    raise AssertionError(f"no event titled {needle!r} in {titles(result)}")


class Router:
    """A ``httpx.MockTransport`` handler recording every URL it is asked for.

    Routes on the ``page`` query parameter, which is what the adapter varies.
    """

    def __init__(
        self,
        pages: dict[str | None, httpx.Response],
        *,
        fallback: httpx.Response | None = None,
    ) -> None:
        self.pages = pages
        self.fallback = fallback
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        self.requests.append(request)
        page = request.url.params.get("page")
        response = self.pages.get(page, self.fallback)
        if response is None:
            # The live behavior past the end of the listing: 200 with `[]`,
            # never an error. An adapter that paginated until-error would keep
            # going from here.
            return httpx.Response(
                200,
                content=b"[]",
                headers={"Content-Type": "application/json", **wp_headers()},
            )
        return httpx.Response(
            response.status_code,
            content=response.content,
            headers=dict(response.headers),
        )

    @property
    def urls(self) -> list[str]:
        return [str(request.url) for request in self.requests]

    @property
    def pages_requested(self) -> list[str | None]:
        return [request.url.params.get("page") for request in self.requests]

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def json_response(
    body: str, status_code: int = 200, headers: dict[str, str] | None = None
) -> httpx.Response:
    return httpx.Response(
        status_code,
        content=body.encode("utf-8"),
        headers={"Content-Type": "application/json; charset=UTF-8", **(headers or {})},
    )


def noop_sleep(seconds: float) -> None:
    """The registry's 2 s per host. Not in a test suite."""


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
    source = next(s for s in space.sources if s.adapter == "json")
    return SourceRef(space, source)


@pytest.fixture
def nexus_ref(registry: Registry) -> SourceRef:
    space = registry.space("maker-nexus")
    source = next(s for s in space.sources if s.adapter == "json")
    return SourceRef(space, source)


def make_fetcher(registry: Registry, router: Router, tmp_path: Path) -> Fetcher:
    return Fetcher(
        registry,
        transport=router.transport(),
        raw_dir=tmp_path / "raw",
        sleep=noop_sleep,
        clock=FakeClock(),
    )


def four_page_router() -> Router:
    """The live shape: 4 pages, ``X-WP-Total: 353``, ``X-WP-TotalPages: 4``."""
    return Router(
        {
            None: json_response(load("crucible-products-p1.json"), headers=wp_headers()),
            "2": json_response(load("crucible-products-p2.json"), headers=wp_headers()),
            "3": json_response(load("crucible-products-p3.json"), headers=wp_headers()),
            "4": json_response(load("crucible-products-p4.json"), headers=wp_headers()),
        }
    )


# ======================================================================= wiring


def test_parse_is_the_registry_entry_point():
    """``sources.yaml``'s ``adapter: json`` resolves to ``parse``."""
    assert parse is parse_json


def test_the_dispatch_table_runs_this_adapter():
    entry = ADAPTERS["json"]
    assert entry.implemented
    assert entry.parse is parse_json
    assert entry.issue == "0022"
    # It needs the fetcher (4 pages) *and* the ref (shape, min_total), which is
    # exactly what `paginates` hands over.
    assert entry.paginates


def test_both_registered_json_sources_are_runnable(registry: Registry):
    runnable = [
        ref
        for ref in registry.all_sources
        if ref.source.adapter == "json" and is_runnable(ref)
    ]
    assert {ref.space.id for ref in runnable} == {"the-crucible", "maker-nexus"}


def test_every_json_source_in_the_registry_names_a_known_shape(registry: Registry):
    for ref in registry.all_sources:
        if ref.source.adapter == "json":
            assert ref.source.shape in SHAPES, ref.source.label


def test_the_registry_pins_orderby_date_on_the_crucible(crucible_ref: SourceRef):
    """The parameter is in ``sources.yaml``, not in the adapter's URL builder."""
    assert crucible_ref.source.params["orderby"] == "date"
    assert crucible_ref.source.params["per_page"] == 100
    assert "orderby=date" in str(request_url(crucible_ref.source))


def test_a_json_source_without_a_shape_is_a_load_error():
    with pytest.raises(Exception) as excinfo:
        Source(adapter="json", url="https://example.org/events.json")
    assert "shape" in str(excinfo.value)


def test_a_json_source_with_an_unknown_shape_is_a_load_error():
    with pytest.raises(Exception) as excinfo:
        Source(adapter="json", url="https://example.org/e.json", shape="not_a_shape")
    assert "not_a_shape" in str(excinfo.value)


def test_shape_is_rejected_on_adapters_that_do_not_take_one():
    with pytest.raises(Exception) as excinfo:
        Source(adapter="ics", url="https://example.org/e.ics", shape="amilia_activities_cache")
    assert "shape" in str(excinfo.value)


def test_a_registry_naming_a_bad_shape_fails_to_load(tmp_path: Path):
    path = tmp_path / "sources.yaml"
    path.write_text(
        "defaults:\n"
        "  timezone: America/Los_Angeles\n"
        "  user_agent: 'bayarea-maker-calendar/0.1 (+${MAKER_CALENDAR_CONTACT})'\n"
        "spaces:\n"
        "  - id: x\n"
        "    name: X\n"
        "    city: Oakland\n"
        "    region: east-bay\n"
        "    url: https://example.org/\n"
        "    sources:\n"
        "      - adapter: json\n"
        "        url: https://example.org/e.json\n"
        "        shape: nope\n",
        encoding="utf-8",
    )
    with pytest.raises(RegistryError):
        load_registry(path, env={"MAKER_CALENDAR_CONTACT": TEST_CONTACT})


# ============================================================ Maker Nexus: the shape


def test_the_dict_keyed_document_parses():
    result = parse_nexus()

    assert result.ok
    assert result.problem is JsonProblem.NONE
    assert result.shape == "amilia_activities_cache"
    assert result.item_count == 5
    # Four records carry a StartDate; the fifth is counted as undated.
    assert result.offering_count == 4
    assert result.undated_item_count == 1
    # One is beyond the 120-day horizon and is clipped here, not downstream.
    assert result.event_count == 3
    assert titles(result) == ["Board Game Night", "3D Printing Thursdays", "Sew & Tell"]


def test_the_document_key_becomes_the_uid():
    """``YYYY-MM-DD_<amiliaId>`` is a ready-made stable identifier: it already
    carries the occurrence date, which is what CLAUDE.md's UID rule asks for."""
    result = parse_nexus()

    assert uids(result) == [
        "2026-08-06_2314915",
        "2026-08-13_2314916",
        "2026-10-22_2318844",
    ]
    event = by_title(result, "Board Game")
    assert event.item_key == "2026-08-06_2314915"
    # And it is the key, not the Id field that sits next to it.
    assert event.uid != "2314915"


def test_document_items_refuses_a_list_for_a_keyed_shape():
    """A shape change under the same URL, reported rather than read as empty."""
    assert document_items([{"Name": "x"}], AMILIA_ACTIVITIES_CACHE.field_map) == []


def test_a_list_where_a_keyed_dict_belongs_is_reported():
    result = parse_json(
        nexus(body='[{"Name": "Board Game Night"}]'),
        shape="amilia_activities_cache",
        today=TODAY,
        now=NOW,
    )
    assert not result.ok
    assert result.problem is JsonProblem.NO_ITEMS
    assert "dict of item objects keyed by identifier" in (result.error or "")


def test_the_minus_seven_offset_survives():
    """``StartDate`` is ISO-8601 with a literal ``-07:00``. It is an instant, so
    nothing here has to guess — and the offset is kept as the source wrote it."""
    event = by_title(parse_nexus(), "Board Game")

    assert event.start == dt.datetime(
        2026, 8, 6, 18, 0, tzinfo=dt.timezone(-dt.timedelta(hours=7))
    )
    assert event.start.utcoffset() == -dt.timedelta(hours=7)
    assert event.start.astimezone(dt.timezone.utc) == dt.datetime(
        2026, 8, 7, 1, 0, tzinfo=dt.timezone.utc
    )
    assert event.source_tz == "-07:00"
    assert event.dtstart_form == "tzid"


def test_tz_carries_a_resolvable_iana_name_not_the_offset():
    """``normalize.day_start_utc`` and issue 0016's health filter both call
    ``ZoneInfo`` on this, and that filter runs outside the per-source try."""
    for event in parse_nexus():
        assert event.tz == "America/Los_Angeles"
        ZoneInfo(event.tz)


def test_the_end_date_maps_through():
    event = by_title(parse_nexus(), "Board Game")
    assert event.end is not None
    assert event.end.astimezone(PACIFIC).hour == 21


def test_price_and_spots_remaining_map_through():
    """The two fields that make this source richer than the same space's gCal."""
    paid = by_title(parse_nexus(), "3D Printing")
    free = by_title(parse_nexus(), "Board Game")

    assert paid.price == "$15"
    assert paid.spots_remaining == 4
    assert paid.max_attendance == 12
    assert free.price == "$0"
    assert free.spots_remaining == 24
    assert free.max_attendance == 30


def test_the_price_is_source_text_not_a_float():
    assert isinstance(by_title(parse_nexus(), "3D Printing").price, str)


def test_dedupe_already_prefers_this_source_for_price():
    """Issue 0015 encodes the finding; this asserts the two agree on the label.

    Capacity is the other documented preference and is deliberately *not* in
    ``dedupe.py``: the canonical record has no capacity field yet, so declaring
    it there would be validated and rejected. It rides on the event instead.
    """
    preference = next(
        pref
        for pref in DEFAULT_FIELD_PREFERENCES
        if pref.space_id == "maker-nexus"
    )
    assert preference.source_label == "amilia-community-events-cache"
    assert "price" in preference.fields
    assert by_title(parse_nexus(), "Board Game").spots_remaining is not None


def test_the_registry_label_matches_the_dedupe_preference(nexus_ref: SourceRef):
    assert nexus_ref.source.label == "amilia-community-events-cache"


def test_html_entities_in_a_name_are_decoded():
    assert "Sew & Tell" in titles(parse_nexus())


def test_an_undated_record_is_counted_never_emitted():
    """A dateless event is not publishable, and a silently smaller catalog is
    the failure this project keeps designing against."""
    result = parse_nexus()

    assert result.undated_item_count == 1
    assert "2026-09-02_2314920" in result.undated_ids
    assert "New Member Orientation" not in titles(result)
    assert "carry no usable" in (result.error or "")


def test_horizon_clipping_drops_the_march_open_house():
    result = parse_nexus()
    assert result.window_end == dt.date(2026, 12, 3)
    assert "Spring Open House" not in titles(result)

    # Widen the horizon and it comes back, which is what makes the clip a clip
    # rather than a parse failure.
    wide = parse_nexus(horizon_days=300)
    assert "Spring Open House" in titles(wide)


def test_no_naive_datetime_ever_leaves_the_adapter():
    """The invariant, asserted at the seam rather than trusted."""
    for result in (parse_nexus(), parse_crucible_page_1()):
        for event in result:
            assert isinstance(event.start, dt.datetime)
            assert event.start.tzinfo is not None
            if isinstance(event.end, dt.datetime):
                assert event.end.tzinfo is not None


def test_the_events_are_sorted_by_start():
    starts = [event.start for event in parse_nexus()]
    assert starts == sorted(starts)


def test_the_events_reach_normalize_with_the_price_intact(registry: Registry):
    """``JsonEvent`` subclasses ``IcsEvent`` so ``from_ics_event`` consumes it."""
    space = registry.space("maker-nexus")
    normalized = normalize_ics(
        parse_nexus(), space=space, source_label="amilia-community-events-cache", now=NOW
    )

    assert normalized.event_count == 3
    prices = {event.title: event.price for event in normalized.events}
    assert prices["3D Printing Thursdays"] == "$15"
    # UID stability: the document key, namespaced by space and nothing else.
    assert any(event.uid == "maker-nexus:2026-08-06_2314915" for event in normalized.events)


# ================================================= The Crucible: the orderby=date trap


def test_the_body_without_orderby_date_looks_perfectly_healthy():
    """**Why the two guards below exist.**

    The amputated response is not malformed in any way a parser can see: 200,
    ``application/json``, well-formed products, real dates, no error field. Fed
    to the parser with no request and no headers to check, it produces a clean,
    plausible, and *wrong* calendar. Nothing in the payload can save us.
    """
    result = parse_json_text(
        load("crucible-products-unordered.json"),
        shape="woocommerce_store_products",
        space_id="the-crucible",
        label="woocommerce-store-api",
        today=TODAY,
        now=NOW,
    )

    assert result.ok
    assert result.event_count == 1
    assert titles(result) == ["Bike Shop Open Hours"]


def test_a_request_without_orderby_date_is_reported_not_accepted():
    """**The headline.** The load-bearing parameter is checked on the URL that
    actually went out, before the body is read at all.

    Without it this endpoint returns HTTP 200, valid JSON and ``X-WP-Total: 98``
    instead of 353 — 72% of the catalog silently missing, covering only 40 of
    the 216 live courses. The previous test shows the body cannot betray it.
    """
    result = parse_json(
        fetched(
            load("crucible-products-unordered.json"),
            url=CRUCIBLE_URL_NO_ORDERBY,
            headers=wp_headers(total=AMPUTATED_TOTAL, pages=1),
        ),
        shape="woocommerce_store_products",
        min_total=300,
        today=TODAY,
        now=NOW,
    )

    assert not result.ok
    assert result.problem is JsonProblem.MISSING_REQUIRED_PARAM
    assert result.event_count == 0
    error = result.error or ""
    assert "orderby" in error and "date" in error
    assert "98" in error and "353" in error


def test_the_same_document_parses_once_orderby_date_is_on_the_request():
    """The guard is a guard, not a blanket refusal."""
    result = parse_crucible_page_1(min_total=300)
    assert result.ok
    assert result.problem is JsonProblem.NONE


def test_a_shrunken_total_is_reported_even_when_the_request_looked_right():
    """The second, independent check: ``X-WP-Total`` against the registry floor.

    It deliberately does not trust the parameter check — the parameter can be
    present and the *server* can change its mind, and a catalog that loses 72%
    overnight is not a quiet week.
    """
    result = parse_json(
        fetched(
            load("crucible-products-unordered.json"),
            url=CRUCIBLE_URL,  # orderby=date IS present
            headers=wp_headers(total=AMPUTATED_TOTAL, pages=1),
        ),
        shape="woocommerce_store_products",
        min_total=300,
        today=TODAY,
        now=NOW,
    )

    assert not result.ok
    assert result.problem is JsonProblem.TRUNCATED_CATALOG
    assert result.event_count == 0
    assert result.reported_total == AMPUTATED_TOTAL
    assert result.expected_min_total == 300
    assert "98" in (result.error or "")


def test_the_registry_declares_the_floor(crucible_ref: SourceRef):
    """``min_total`` is registry data, not a constant in the adapter."""
    assert crucible_ref.source.min_total == 300
    assert crucible_ref.source.min_total < LIVE_TOTAL


def test_the_floor_comes_from_the_registry_entry_when_no_kwarg_is_given(
    crucible_ref: SourceRef,
):
    result = parse_json(
        fetched(
            load("crucible-products-unordered.json"),
            headers=wp_headers(total=AMPUTATED_TOTAL, pages=1),
        ),
        ref=crucible_ref,
        today=TODAY,
        now=NOW,
    )
    assert result.problem is JsonProblem.TRUNCATED_CATALOG
    assert result.expected_min_total == 300


def test_a_missing_total_header_is_reported_rather_than_waved_through():
    """Losing the guard is itself the failure: a source we can no longer check
    is not a source to publish on trust."""
    result = parse_crucible_page_1(
        result=fetched(load("crucible-products-p1.json")), min_total=300
    )

    assert not result.ok
    assert result.problem is JsonProblem.NO_TOTAL
    assert "x-wp-total" in (result.error or "")


def test_the_end_to_end_fetch_refuses_the_amputated_catalog(
    registry: Registry, crucible_ref: SourceRef, tmp_path: Path
):
    """Through the real registry URL and the real fetcher, mock transport only."""
    router = Router(
        {
            None: json_response(
                load("crucible-products-unordered.json"),
                headers=wp_headers(total=AMPUTATED_TOTAL, pages=1),
            )
        }
    )
    fetcher = make_fetcher(registry, router, tmp_path)
    try:
        result = fetch_json(fetcher, crucible_ref, today=TODAY, now=NOW)
    finally:
        fetcher.close()

    assert not result.ok
    assert result.problem is JsonProblem.TRUNCATED_CATALOG
    # The request itself was correct; only the response gave it away.
    assert "orderby=date" in router.urls[0]


def test_missing_required_params_reports_what_was_actually_sent():
    assert missing_required_params(CRUCIBLE_URL, (("orderby", "date"),)) == []
    assert missing_required_params(CRUCIBLE_URL_NO_ORDERBY, (("orderby", "date"),)) == [
        ("orderby", "date", None)
    ]
    assert missing_required_params(
        f"{CRUCIBLE_BASE}?orderby=popularity", (("orderby", "date"),)
    ) == [("orderby", "date", "popularity")]


# ============================================================ The Crucible: pagination


def test_pagination_follows_x_wp_totalpages(
    registry: Registry, crucible_ref: SourceRef, tmp_path: Path
):
    """Four pages because the header says four — not because page 5 failed."""
    router = four_page_router()
    fetcher = make_fetcher(registry, router, tmp_path)
    try:
        result = fetch_json(fetcher, crucible_ref, today=TODAY, now=NOW)
    finally:
        fetcher.close()

    assert result.ok, result.error
    assert result.reported_total == LIVE_TOTAL
    assert result.reported_pages == LIVE_PAGES
    assert result.page_count == LIVE_PAGES
    assert router.pages_requested == [None, "2", "3", "4"]
    assert result.item_count == 6
    assert result.offering_count == 9  # every pa_class-date term, sentinel included
    assert result.event_count == 6


def test_pagination_never_walks_until_error(
    registry: Registry, crucible_ref: SourceRef, tmp_path: Path
):
    """``page=99`` answers 200 with ``[]``, so an until-error walk would keep
    going to the ceiling and then call the result healthy.

    The router's fallback *is* that behavior: any page it does not know about
    answers 200 with an empty list. Page 5 is never asked for.
    """
    router = four_page_router()
    fetcher = make_fetcher(registry, router, tmp_path)
    try:
        result = fetch_json(fetcher, crucible_ref, today=TODAY, now=NOW)
    finally:
        fetcher.close()

    assert result.ok
    assert "5" not in router.pages_requested
    assert "99" not in router.pages_requested
    assert len(router.requests) == LIVE_PAGES


def test_a_page_99_style_empty_list_is_handled_not_crashed():
    """The negative control from the survey, as a document: 200 with ``[]``."""
    result = parse_json(
        fetched(load("crucible-products-empty.json"), headers=wp_headers(pages=1)),
        shape="woocommerce_store_products",
        today=TODAY,
        now=NOW,
    )

    assert not result.ok
    assert result.problem is JsonProblem.NO_ITEMS
    assert result.event_count == 0
    assert "empty body where a catalog belongs" in (result.error or "")


def test_an_empty_page_before_the_last_stops_the_walk_and_says_so(
    registry: Registry, crucible_ref: SourceRef, tmp_path: Path
):
    """The header and the body disagreeing is a note, not a silent stop."""
    router = Router(
        {
            None: json_response(load("crucible-products-p1.json"), headers=wp_headers()),
            "2": json_response(load("crucible-products-p2.json"), headers=wp_headers()),
            "3": json_response(load("crucible-products-empty.json"), headers=wp_headers()),
        }
    )
    fetcher = make_fetcher(registry, router, tmp_path)
    try:
        result = fetch_json(fetcher, crucible_ref, today=TODAY, now=NOW)
    finally:
        fetcher.close()

    assert result.ok
    assert router.pages_requested == [None, "2", "3"]
    assert "empty list" in (result.error or "")
    assert "disagree" in (result.error or "")


def test_every_paginated_request_keeps_orderby_date(
    registry: Registry, crucible_ref: SourceRef, tmp_path: Path
):
    """Page 4 must not be the amputated catalog because we rebuilt the query."""
    router = four_page_router()
    fetcher = make_fetcher(registry, router, tmp_path)
    try:
        fetch_json(fetcher, crucible_ref, today=TODAY, now=NOW)
    finally:
        fetcher.close()

    assert len(router.urls) == LIVE_PAGES
    for url in router.urls:
        assert "orderby=date" in url
        assert "per_page=100" in url


def test_page_url_only_adds_the_page_parameter():
    assert str(page_url(CRUCIBLE_URL, 3)) == f"{CRUCIBLE_URL}&page=3"


def test_a_failed_later_page_fails_the_whole_parse(
    registry: Registry, crucible_ref: SourceRef, tmp_path: Path
):
    """Half a catalog that looks healthy is the failure mode this project keeps
    designing against; carry-forward republishes last night's complete set."""
    router = Router(
        {
            None: json_response(load("crucible-products-p1.json"), headers=wp_headers()),
            "2": json_response("<!DOCTYPE html><html>error</html>", status_code=500),
        }
    )
    fetcher = make_fetcher(registry, router, tmp_path)
    try:
        result = fetch_json(fetcher, crucible_ref, today=TODAY, now=NOW)
    finally:
        fetcher.close()

    assert not result.ok
    assert result.problem is JsonProblem.PAGE_FAILED
    assert result.event_count == 0
    assert "not published as if they were the whole catalog" in (result.error or "")


def test_a_single_page_call_says_it_only_read_one_page():
    """No fetcher means page 1 only, and that is reported rather than silent."""
    result = parse_crucible_page_1()

    assert result.ok
    assert result.truncated
    assert result.page_count == 1
    assert "only page 1 was read" in (result.error or "")


def test_a_totalpages_header_above_the_ceiling_is_refused(
    registry: Registry, crucible_ref: SourceRef, tmp_path: Path
):
    router = Router(
        {None: json_response(load("crucible-products-p1.json"), headers=wp_headers(pages=900))}
    )
    fetcher = make_fetcher(registry, router, tmp_path)
    try:
        result = fetch_json(fetcher, crucible_ref, today=TODAY, now=NOW)
    finally:
        fetcher.close()

    assert result.problem is JsonProblem.RUNAWAY_PAGINATION
    assert len(router.requests) == 1


# ================================================== The Crucible: dates and the sentinel


def test_mm_dd_yy_ampm_parses_to_the_right_local_time():
    """``08/06/26 6:00 pm`` is the exact value the product page prints, and it
    decodes to the same instant the ``embedded_json`` blob's epoch does."""
    parsed, epoch = parse_us_short("08/06/26 6:00 pm", zone=PACIFIC)

    assert parsed == dt.datetime(2026, 8, 6, 18, 0, tzinfo=PACIFIC)
    assert parsed.utcoffset() == -dt.timedelta(hours=7)  # PDT
    # 1786064400 is the value ac-course-data carries for the same offering.
    assert epoch == 1786064400


def test_a_winter_date_lands_on_the_other_side_of_the_dst_boundary():
    parsed, _ = parse_us_short("11/12/26 10:00 am", zone=PACIFIC)
    assert parsed.utcoffset() == -dt.timedelta(hours=8)  # PST
    assert parsed.hour == 10  # and the student still reads "10:00 am"


def test_the_sentinel_would_otherwise_land_in_2070():
    """**Why the drop exists.** Pinned so it cannot quietly stop being true.

    ``dateutil`` resolves a two-digit year against a window centred on the
    current year, so ``70`` becomes 2070 rather than 1970 — a date inside no
    horizon and outside no sanity window anybody wrote down. It would be
    published, and it would stay published.
    """
    naive = dateutil_parser.parse(EPOCH_SENTINEL)

    assert naive.year == 2070
    assert naive > dt.datetime(2026, 8, 5) + dt.timedelta(days=120)


def test_the_epoch_sentinel_is_dropped_and_counted():
    result = parse_crucible_page_1()

    assert result.sentinel_count == 1
    assert all(event.start.year != 2070 for event in result)
    assert all(event.start.year != 1970 for event in result)
    assert "2070" in (result.error or "")
    # And it is dropped as *text*, before any parser sees it.
    assert EPOCH_SENTINEL in (result.error or "")


def test_the_sentinel_is_matched_case_and_space_insensitively():
    document = stdlib_json.dumps(
        [
            {
                "id": 1,
                "name": "Sentinel Only",
                "attributes": [
                    {
                        "taxonomy": "pa_class-date",
                        "terms": [{"id": 1, "name": "  01/01/70 12:00 AM  "}],
                    }
                ],
            }
        ]
    )
    result = parse_json(
        fetched(document, headers=wp_headers(pages=1)),
        shape="woocommerce_store_products",
        today=TODAY,
        now=NOW,
    )

    assert not result.ok
    # A catalog with items and not one usable date is a schema change, not a
    # quiet week — and here the one date it had was the sentinel.
    assert result.problem is JsonProblem.NO_DATES
    assert result.sentinel_count == 1


def test_a_year_outside_the_sanity_range_is_dropped():
    """The second net, for the sentinel nobody has met yet."""
    assert parse_us_short("01/01/70 12:00 am", zone=PACIFIC) == (None, None)
    assert parse_us_short("06/01/2085 9:00 am", zone=PACIFIC) == (None, None)


def test_an_unparseable_date_is_counted_not_guessed():
    document = stdlib_json.dumps(
        [
            {
                "id": 2,
                "name": "Rubbish Date",
                "attributes": [
                    {
                        "taxonomy": "pa_class-date",
                        "terms": [
                            {"id": 1, "name": "next Tuesday-ish"},
                            {"id": 2, "name": "09/03/26 7:00 pm"},
                        ],
                    }
                ],
            }
        ]
    )
    result = parse_json_text(
        document, shape="woocommerce_store_products", today=TODAY, now=NOW
    )

    assert result.ok
    assert result.bad_date_count == 1
    assert result.event_count == 1


# ============================================ The Crucible: fields, UIDs, and categories


def test_one_product_becomes_one_event_per_class_date_term():
    result = parse_crucible_page_1()

    assert result.item_count == 2
    # 3 terms on the first product (one of them the sentinel) + 1 on the second.
    assert result.offering_count == 4
    assert result.event_count == 3
    assert titles(result) == [
        "Blacksmithing I – 5 weeks",
        "Blacksmithing I – 5 weeks",
        "Glass Fusing & Slumping Lab",
    ]


def test_the_uid_is_product_id_and_term_id():
    """Both are WordPress ids, so it is stable across runs by construction — and
    it is deliberately *not* the blob's ``{id}:{start_epoch}``: the two Crucible
    sources are meant to meet in dedupe on title and start, resolved by trust,
    rather than to be silently identical."""
    result = parse_crucible_page_1()

    assert uids(result) == ["12345:7011", "12345:7012", "22222:7101"]
    event = result.events[0]
    assert event.item_id == "12345"
    assert event.term_id == "7011"
    assert event.uid != f"12345:{event.start_epoch}"


def test_the_woocommerce_categories_are_not_published():
    """60+ of them are one-off per-product categories equal to the class title,
    and issue 0010's ``categories_exclude`` runs on whatever lands here. Dedupe
    already says the blob's ``department`` owns this field."""
    event = by_title(parse_crucible_page_1(), "Blacksmithing")

    assert event.categories == ()
    # Kept as diagnostics, so the pollution is visible rather than invented.
    assert event.source_categories == ("Blacksmithing I", "Blacksmithing")


def test_the_blob_wins_categories_in_dedupe():
    preference = next(
        pref
        for pref in DEFAULT_FIELD_PREFERENCES
        if pref.space_id == "the-crucible" and "categories" in pref.fields
    )
    assert preference.source_label == "course-catalog-blob"


def test_the_woocommerce_api_wins_description_and_price_in_dedupe():
    preference = next(
        pref
        for pref in DEFAULT_FIELD_PREFERENCES
        if pref.space_id == "the-crucible" and pref.source_label == "woocommerce-store-api"
    )
    assert set(preference.fields) == {"description", "price"}


def test_minor_unit_prices_become_source_text():
    result = parse_crucible_page_1()

    assert by_title(result, "Blacksmithing").price == "$495"
    # A variable product's range stays a range rather than becoming one number.
    assert by_title(result, "Glass Fusing").price == "$95 - $125"


def test_woo_price_text_keeps_a_non_numeric_price_verbatim():
    assert woo_price_text({"price": "call for pricing"}) == "call for pricing"
    assert woo_price_text(None) is None


def test_descriptions_are_stripped_of_tags():
    event = by_title(parse_crucible_page_1(), "Blacksmithing")
    assert event.description == "Forge basics over five evenings."


def test_strip_tags_decodes_entities_after_stripping():
    assert strip_tags("<p>ages 12&#8211;18</p>") == "ages 12–18"
    assert strip_tags("<p>&lt;p&gt; is a tag</p>") == "<p> is a tag"


def test_stock_state_is_carried_and_the_event_is_still_published():
    """A sold-out class is still a real public event."""
    assert by_title(parse_crucible_page_1(), "Glass Fusing").in_stock is False


def test_no_location_is_invented():
    """The catalog carries no location field of any kind — leaving it None hands
    the decision to VenuePolicy and the space's mandatory address_override."""
    for event in parse_crucible_page_1():
        assert event.location is None


def test_horizon_clipping_across_pages(
    registry: Registry, crucible_ref: SourceRef, tmp_path: Path
):
    router = four_page_router()
    fetcher = make_fetcher(registry, router, tmp_path)
    try:
        result = fetch_json(fetcher, crucible_ref, today=TODAY, now=NOW)
    finally:
        fetcher.close()

    days = [event.start.date() for event in result.events]
    assert min(days) >= dt.date(2026, 8, 5)
    assert max(days) <= dt.date(2026, 12, 3)
    # The 2025 term and the 2026-12-20 term are both real and both outside.
    assert dt.date(2025, 6, 13) not in days
    assert dt.date(2026, 12, 20) not in days
    assert result.offering_count > result.event_count


def test_a_product_with_no_class_date_attribute_is_counted_as_undated(
    registry: Registry, crucible_ref: SourceRef, tmp_path: Path
):
    router = four_page_router()
    fetcher = make_fetcher(registry, router, tmp_path)
    try:
        result = fetch_json(fetcher, crucible_ref, today=TODAY, now=NOW)
    finally:
        fetcher.close()

    assert result.undated_item_count == 1
    assert "33333" in result.undated_ids
    assert "Gift Certificate" not in titles(result)


def test_woo_term_values_matches_on_taxonomy_and_on_the_human_name():
    attributes = [
        {"name": "Colour", "taxonomy": "pa_colour", "terms": [{"id": 1, "name": "Red"}]},
        {"name": "Class Date", "taxonomy": "pa_class-date", "terms": [{"id": 2, "name": "x"}]},
    ]
    assert woo_term_values(attributes, taxonomy="pa_class-date", term_uid="id") == [
        ("2", "x")
    ]

    untaxonomised = [{"name": "Class Date", "terms": [{"id": 3, "name": "y"}]}]
    assert woo_term_values(untaxonomised, taxonomy="pa_class-date", term_uid="id") == [
        ("3", "y")
    ]


# ==================================================================== HTTP 200 is not success


def test_a_200_with_html_is_reported_not_read_as_an_empty_catalog():
    """The rule, applied: a body that is not the document we registered must not
    be read as a catalog with nothing in it."""
    result = parse_json(
        fetched(
            "<!DOCTYPE html><html><head><title>The Crucible</title></head>"
            "<body>rendered page</body></html>",
            content_type="text/html",
            headers=wp_headers(),
        ),
        shape="woocommerce_store_products",
        today=TODAY,
        now=NOW,
    )

    assert not result.ok
    assert result.problem is JsonProblem.WRONG_CONTENT_TYPE
    assert result.event_count == 0
    assert "HTTP 200 is not success" in (result.error or "")
    assert "HTML document" in (result.error or "")


def test_a_200_with_html_under_a_json_content_type_is_still_reported():
    """The Crucible's other recorded trap is a lying *header*; this is the same
    lie in the other direction, and the body is what settles it."""
    result = parse_json(
        fetched(
            "<!DOCTYPE html><html><body>rendered page</body></html>",
            content_type="application/json",
            headers=wp_headers(),
        ),
        shape="woocommerce_store_products",
        today=TODAY,
        now=NOW,
    )

    assert not result.ok
    assert result.problem is JsonProblem.NOT_JSON
    assert "200-with-a-rendered-page" in (result.error or "")


def test_json_served_as_text_plain_is_tolerated():
    """Sloppy, not dishonest — and the body is real JSON."""
    result = parse_crucible_page_1(
        result=fetched(
            load("crucible-products-p1.json"),
            content_type="text/plain",
            headers=wp_headers(),
        )
    )

    assert result.ok
    assert "tolerated" in (result.error or "")


def test_a_404_is_reported():
    result = parse_json(
        fetched("{}", status_code=404),
        shape="woocommerce_store_products",
        today=TODAY,
        now=NOW,
    )
    assert result.problem is JsonProblem.HTTP_ERROR
    assert "HTTP 404" in (result.error or "")


def test_a_304_is_not_a_zero():
    result = parse_json(
        fetched("", outcome=Outcome.NOT_MODIFIED, status_code=304),
        shape="woocommerce_store_products",
        today=TODAY,
        now=NOW,
    )
    assert result.problem is JsonProblem.NOT_MODIFIED
    assert "reuse the stored events" in (result.error or "")


def test_an_empty_body_is_reported():
    result = parse_json(
        fetched(""), shape="woocommerce_store_products", today=TODAY, now=NOW
    )
    assert result.problem is JsonProblem.EMPTY_BODY


def test_no_shape_is_a_configuration_error_not_a_source_failure():
    result = parse_json(fetched(load("crucible-products-p1.json")), today=TODAY, now=NOW)

    assert not result.ok
    assert result.problem is JsonProblem.NO_SHAPE
    assert "would be a guess" in (result.error or "")
    assert "woocommerce_store_products" in (result.error or "")


def test_an_unknown_shape_is_reported():
    result = parse_json(
        fetched(load("crucible-products-p1.json")),
        shape="nope",
        today=TODAY,
        now=NOW,
    )
    assert result.problem is JsonProblem.UNKNOWN_SHAPE


# ==================================================================== reuse from issue 0021


def test_the_field_map_is_issue_0021s_extended_not_reimplemented():
    """One ``FieldMap``, one ``resolve_path``, one ``_money``. Two copies of any
    of them would drift, and the copy that drifted would be the one that started
    turning "sliding scale $10-30" into a number."""
    assert issubclass(JsonFieldMap, FieldMap)
    assert AMILIA_ACTIVITIES_CACHE.field_map.start_format == "iso"
    assert WOOCOMMERCE_STORE_PRODUCTS.field_map.start_format == "us_short"


def test_the_shape_registry_is_the_whole_vocabulary():
    assert set(SHAPES) == {"amilia_activities_cache", "woocommerce_store_products"}
    for name, shape in SHAPES.items():
        assert shape.name == name


def test_only_the_woocommerce_shape_demands_a_parameter():
    assert WOOCOMMERCE_STORE_PRODUCTS.required_params == (("orderby", "date"),)
    assert AMILIA_ACTIVITIES_CACHE.required_params == ()
    assert not AMILIA_ACTIVITIES_CACHE.paginated
    assert WOOCOMMERCE_STORE_PRODUCTS.paginated
