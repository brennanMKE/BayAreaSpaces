"""Tests for the ``bookwhen_html`` adapter (issue 0024).

**No test here touches the network.** Issue 0001 — the bot about page that
``$MAKER_CALENDAR_CONTACT`` points at — is still open, so a live request would
go out under a User-Agent naming a page that does not resolve. Every request in
this file goes through ``httpx.MockTransport`` and every payload is a
hand-authored fixture under ``tests/fixtures/bookwhen_html/``.

The fixture is **structurally faithful, not a copy**: the 20 rows, their
``data-event`` attributes, the ``<button>`` titles, the ``td.duration`` display
strings, the ``data-options`` account id and the 2026-08-13 → 2027-01-05 span
are what the 2026-08-05 survey recorded; Bookwhen's stylesheets and booking
chrome are not.

What is being defended
----------------------

**The adapter is deterministic because of one attribute.**
``data-event="ev-{entryid}-{YYYYMMDDHHMMSS}"`` is a machine-written local start,
so the tests assert *exact aware datetimes* — including a pair either side of
2026-11-01, where the wall clock is identical and the offset is not.

**Zero is only ever published when the page said so.** A page that is not
Bookwhen at all, a Bookwhen page whose rows vanished, and a Bookwhen page
rendering its empty state are three different answers here. Collapsing them into
an empty list is the worst outcome this project has, and it is the one an
HTML-scraping adapter is most likely to produce.

**One bad row costs one row.** A malformed ``data-event`` is skipped and counted;
the other nineteen events still publish.

**UIDs are ``{entryid}:{stamp}``** — the source's own key plus the source's own
timestamp, so two parses of the same bytes produce byte-identical UIDs and no
subscriber sees every event as new.

**It is a fallback and it says so.** Issue 0002 is chasing the Bookwhen ICS
token; when it lands this source is demoted or dropped.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from pipeline.adapters.bookwhen_html import (
    AGENDA_ROW_HOOK,
    DEFAULT_PAGE_SIZE,
    DEFAULT_ZONE,
    EMPTY_HOOKS,
    AgendaRow,
    BookwhenParse,
    BookwhenProblem,
    account_id,
    agenda_rows,
    calendar_items_url,
    data_hooks,
    fetch_bookwhen_html,
    html_from_calendar_items,
    looks_like_bookwhen,
    parse,
    parse_bookwhen_html,
    parse_bookwhen_html_text,
    parse_data_event,
)
from pipeline.adapters.jsonld import ld_blocks
from pipeline.cli import ADAPTERS, implemented_adapters, is_runnable, process_source
from pipeline.config import Registry, SourceRef, load_registry
from pipeline.fetch import FetchResult, Fetcher, Outcome
from pipeline.normalize import normalize_ics

FIXTURES = Path(__file__).parent / "fixtures" / "bookwhen_html"

#: The source-survey date. Every fixture is authored relative to it.
TODAY = dt.date(2026, 8, 5)
NOW = dt.datetime(2026, 8, 5, 3, 15, tzinfo=dt.timezone.utc)
HORIZON_END = dt.date(2026, 12, 3)  # TODAY + 120 days, inclusive

PDT = dt.timezone(dt.timedelta(hours=-7))
PST = dt.timezone(dt.timedelta(hours=-8))

TEST_CONTACT = "https://maker-calendar.test/about"

AGENDA_URL = "https://bookwhen.com/sequoiafabrica"
SPACE_ID = "sequoia-fabrica"
LABEL = "bookwhen-agenda"

#: 20 rows on the page, 16 of them inside a 120-day window opened on 2026-08-05.
ROWS_ON_PAGE = 20
EVENTS_IN_HORIZON = 16


# --------------------------------------------------------------------------- helpers


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fetched(
    body: str | bytes,
    *,
    content_type: str | None = "text/html; charset=utf-8",
    status_code: int = 200,
    outcome: Outcome = Outcome.FETCHED,
    label: str = LABEL,
    space_id: str = SPACE_ID,
    url: str = AGENDA_URL,
    reason: str | None = None,
) -> FetchResult:
    """A :class:`FetchResult` as the fetch layer would hand one to the adapter."""
    payload = body.encode("utf-8") if isinstance(body, str) else body
    return FetchResult(
        space_id=space_id,
        label=label,
        adapter="bookwhen_html",
        url=url,
        outcome=outcome,
        status_code=status_code,
        content_type=content_type,
        body=payload,
        byte_count=len(payload),
        charset="utf-8",
        reason=reason,
    )


def parse_agenda(**kwargs: Any) -> BookwhenParse:
    kwargs.setdefault("today", TODAY)
    kwargs.setdefault("now", NOW)
    return parse_bookwhen_html(fetched(load("sequoiafabrica-agenda.html")), **kwargs)


def parse_text(document: str, **kwargs: Any) -> BookwhenParse:
    kwargs.setdefault("today", TODAY)
    kwargs.setdefault("now", NOW)
    kwargs.setdefault("space_id", SPACE_ID)
    kwargs.setdefault("label", LABEL)
    kwargs.setdefault("source_url", AGENDA_URL)
    return parse_bookwhen_html_text(document, **kwargs)


def row_html(
    data_event: str,
    title: str = "Member Applicant Orientation",
    *,
    duration: str = "6:30 PM &ndash; 7:30 PM PST",
    location: str | None = None,
) -> str:
    """One agenda row, in the shape the live page writes it."""
    cell = f'<td class="location">{location}</td>' if location else ""
    return (
        f'<tr class="agenda__row" data-hook="{AGENDA_ROW_HOOK}" '
        f'data-event="{data_event}">'
        f'<td class="duration">{duration}</td>'
        f'<td class="title"><button type="button">{title}</button></td>'
        f"{cell}</tr>"
    )


def agenda_page(rows: str, *, extra: str = "", options: bool = True) -> str:
    """A minimal Bookwhen shell carrying whatever rows the caller says."""
    attr = (
        " data-options='{\"calendar\":\"lcqebfpp6u7h\",\"view\":\"agenda\"}'"
        if options
        else ""
    )
    return (
        "<!DOCTYPE html><html><head><title>Sequoia Fabrica</title></head><body>"
        f'<div id="calendar" data-hook="calendar_container"{attr}>'
        f'<table class="agenda" data-hook="agenda_list"><tbody>{rows}</tbody></table>'
        f"{extra}</div></body></html>"
    )


def titles(result: BookwhenParse) -> list[str]:
    return [event.title or "" for event in result.events]


def starts(result: BookwhenParse) -> list[dt.datetime]:
    return [event.start for event in result.events]  # type: ignore[misc]


def by_stamp(result: BookwhenParse, stamp: str) -> Any:
    for event in result.events:
        if event.start_stamp == stamp:  # type: ignore[attr-defined]
            return event
    raise AssertionError(f"no event stamped {stamp} in {titles(result)}")


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


def js_response(body: str) -> httpx.Response:
    """``calendar_items`` answers ``text/javascript``, not JSON."""
    return httpx.Response(
        200,
        content=body.encode("utf-8"),
        headers={"Content-Type": "text/javascript; charset=utf-8"},
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
def agenda_ref(registry: Registry) -> SourceRef:
    space = registry.space(SPACE_ID)
    source = next(s for s in space.sources if s.adapter == "bookwhen_html")
    return SourceRef(space, source)


def make_fetcher(registry: Registry, router: Router, tmp_path: Path) -> Fetcher:
    return Fetcher(
        registry,
        transport=router.transport(),
        raw_dir=tmp_path / "raw",
        sleep=noop_sleep,
        clock=FakeClock(),
    )


# --------------------------------------------------------------------------- the rows


def test_rows_are_found_by_their_data_hook():
    """``data-hook`` is Bookwhen's own test hook, which is why it anchors this
    parser rather than a CSS class: hooks exist so *their* tests can find these
    rows, so they move less often than presentation does."""
    rows = agenda_rows(load("sequoiafabrica-agenda.html"))

    assert len(rows) == ROWS_ON_PAGE
    assert all(isinstance(row, AgendaRow) for row in rows)
    assert all(row.usable for row in rows)
    # The header row and the "Show more" button are not agenda rows, and the
    # page carries both.
    hooks = data_hooks(load("sequoiafabrica-agenda.html"))
    assert "agenda_list_header" in hooks
    assert "agenda_list_more" in hooks


def test_the_page_offers_nothing_better_which_is_why_this_adapter_exists():
    """No JSON-LD, no ``webcal://``, no ``.ics``, no ``<link rel=alternate>``.

    Confirmed live on 2026-08-05 and reproduced in the fixture. If any of these
    were there, this adapter would be the wrong answer and an ``ics`` or
    ``jsonld`` source would be the right one.
    """
    page = load("sequoiafabrica-agenda.html")

    assert ld_blocks(page) == ()
    assert "webcal://" not in page
    assert ".ics" not in page
    assert 'rel="alternate"' not in page


def test_the_default_page_returns_twenty_rows_reaching_past_the_horizon():
    """20 rows, 2026-08-13 → 2027-01-05. That span is *why* pagination is not
    the default path: one request already covers a 120-day window."""
    result = parse_agenda()

    assert result.ok
    assert result.row_count == ROWS_ON_PAGE == DEFAULT_PAGE_SIZE
    assert result.last_row_start == dt.date(2027, 1, 5)
    assert starts(result)[0] == dt.datetime(2026, 8, 13, 18, 0, tzinfo=PDT)
    assert result.looks_paginated is False


def test_the_calendar_id_is_read_off_the_page_rather_than_guessed():
    """``lcqebfpp6u7h`` — the same string the ICS lead in ``spaces/`` records,
    recovered from the page's own ``data-options`` blob."""
    assert account_id(load("sequoiafabrica-agenda.html")) == "lcqebfpp6u7h"
    assert parse_agenda().calendar_id == "lcqebfpp6u7h"


# --------------------------------------------------------------------------- the attribute


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("ev-sfhandemb1-20260813180000", ("sfhandemb1", dt.datetime(2026, 8, 13, 18, 0))),
        # The entry id is opaque and may contain hyphens. The stamp is anchored
        # to the end, so the *last* 14-digit group wins.
        ("ev-sf-bioyarn-20261029180000", ("sf-bioyarn", dt.datetime(2026, 10, 29, 18, 0))),
        ("ev-x-20270105183000", ("x", dt.datetime(2027, 1, 5, 18, 30))),
    ],
)
def test_data_event_decodes_to_the_exact_local_start(
    value: str, expected: tuple[str, dt.datetime]
):
    assert parse_data_event(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        None,
        "ev-sfmao01",  # no stamp
        "ev-20260813180000",  # no entry id
        "sfmao01-20260813180000",  # no ev- prefix
        "ev-sfmao01-2026081318000",  # 13 digits
        "ev-sfmao01-99999999999999",  # 14 digits that are not a date
        "ev-sfmao01-20260231180000",  # 31 February
    ],
)
def test_a_data_event_that_is_not_the_documented_shape_decodes_to_nothing(
    value: str | None,
):
    """Returning ``None`` is what lets the caller *count* the row instead of
    silently dropping it — and a wrong date read out of a malformed attribute
    would be wrong data that looks like good data."""
    assert parse_data_event(value) is None


def test_the_start_is_exact_and_aware_on_every_row():
    result = parse_agenda()

    assert starts(result)[:3] == [
        dt.datetime(2026, 8, 13, 18, 0, tzinfo=PDT),
        dt.datetime(2026, 8, 18, 18, 30, tzinfo=PDT),
        dt.datetime(2026, 8, 25, 18, 0, tzinfo=PDT),
    ]
    assert all(event.start.tzinfo is not None for event in result.events)
    # Nothing naive ever leaves this module; normalize.py would fail the run.
    assert all(
        event.start.utcoffset() is not None for event in result.events  # type: ignore[union-attr]
    )


def test_the_start_is_right_on_both_sides_of_the_dst_boundary():
    """DST ended 2026-11-01. Two rows written by the same server, two different
    offsets, and a human reading either one sees roughly 6 PM.

    This is the assertion that would fail first if the stamp were ever read as
    UTC, or attached with a fixed ``-07:00`` offset instead of a real zone.
    """
    result = parse_agenda()
    before = by_stamp(result, "20261029180000")  # Let's make BioYarn!, PDT
    after = by_stamp(result, "20261103183000")  # Orientation, PST

    assert before.start == dt.datetime(2026, 10, 29, 18, 0, tzinfo=PDT)
    assert after.start == dt.datetime(2026, 11, 3, 18, 30, tzinfo=PST)
    assert (before.source_tz, after.source_tz) == ("-07:00", "-08:00")
    # Same zone, different offset — which is only possible because tz is an
    # IANA name and not the offset itself.
    assert before.tz == after.tz == DEFAULT_ZONE
    assert before.start.astimezone(dt.timezone.utc) == dt.datetime(
        2026, 10, 30, 1, 0, tzinfo=dt.timezone.utc
    )
    assert after.start.astimezone(dt.timezone.utc) == dt.datetime(
        2026, 11, 4, 2, 30, tzinfo=dt.timezone.utc
    )


def test_times_are_pacific_and_the_zone_is_a_resolvable_iana_name():
    """:func:`pipeline.normalize.day_start_utc` and issue 0016's health filter
    both call ``ZoneInfo`` on ``tz``, and that filter runs *outside* the
    per-source try/except. An offset string there would take down the run."""
    from zoneinfo import ZoneInfo

    result = parse_agenda()

    assert {event.tz for event in result.events} == {"America/Los_Angeles"}
    assert ZoneInfo(result.events[0].tz or "")  # resolvable, not "-07:00"
    assert {event.dtstart_form for event in result.events} == {"tzid"}


# --------------------------------------------------------------------------- the title


def test_the_title_is_the_rows_button_text():
    result = parse_agenda()

    assert titles(result)[0] == "Hand Embroidery Social"
    assert titles(result).count("Member Applicant Orientation") == 8  # inside horizon
    assert "Let's make BioYarn!" in titles(result)
    assert "Upmending (upcycling + mending) Social" in titles(result)


def test_an_entity_in_a_button_is_decoded_on_the_way_out():
    """The page writes ``Crochet &amp; Knitting Social``. A calendar full of
    ``&amp;`` is the kind of thing nobody files a bug for."""
    assert "Crochet & Knitting Social" in titles(parse_agenda())
    assert not any("&amp;" in title for title in titles(parse_agenda()))


def test_the_title_comes_from_the_button_and_not_from_the_other_cells():
    """``td.duration`` sits in the same row and is display text. Reading the
    row's whole text content would produce "Thu 13 Aug 6:00 PM – 8:00 PM PDT
    Hand Embroidery Social Book"."""
    result = parse_text(
        agenda_page(
            row_html("ev-one-20260901180000", "Open Shop", duration="6:00 PM PDT")
        )
    )

    assert titles(result) == ["Open Shop"]
    assert result.events[0].time_text == "6:00 PM PDT"  # type: ignore[attr-defined]


def test_the_duration_string_is_carried_and_never_becomes_an_end_time():
    """The only end time on this page is inside a display string whose format we
    have seen one variant of. ``DTSTART + a guess`` renders a wrong block in
    every calendar client; the ICS feed issue 0002 is chasing has a real
    ``DTEND``, which is one more reason to prefer it."""
    result = parse_agenda()

    assert result.events[0].time_text == "6:00 PM – 8:00 PM PDT"  # type: ignore[attr-defined]
    assert all(event.end is None for event in result.events)
    assert all(not event.all_day for event in result.events)


def test_a_row_with_no_button_still_publishes_and_is_counted():
    rows = row_html("ev-one-20260901180000", "Open Shop") + (
        f'<tr data-hook="{AGENDA_ROW_HOOK}" data-event="ev-two-20260902180000">'
        '<td class="duration">6:00 PM PDT</td></tr>'
    )
    result = parse_text(agenda_page(rows))

    assert result.ok
    assert result.event_count == 2
    assert result.untitled_row_count == 1
    assert "no <button> text" in (result.error or "")


# --------------------------------------------------------------------------- one bad row


def test_a_malformed_data_event_costs_one_row_and_not_the_others():
    """**The failure mode an HTML parser is most likely to have.** One row with a
    reformatted attribute must not take the other nineteen events off the
    calendar."""
    page = load("sequoiafabrica-agenda.html").replace(
        'data-event="ev-sfmao05-20261020183000"', 'data-event="ev-sfmao05-BROKEN"'
    )
    result = parse_text(page)

    assert result.ok
    assert result.row_count == ROWS_ON_PAGE
    assert result.bad_row_count == 1
    assert result.bad_row_values == ("ev-sfmao05-BROKEN",)
    assert result.event_count == EVENTS_IN_HORIZON - 1
    assert "ev-sfmao05-BROKEN" in (result.error or "")
    # And the rows either side of it are untouched.
    assert by_stamp(result, "20261008180000").title == "Hand Embroidery Social"
    assert by_stamp(result, "20261027180000").title == "Crochet & Knitting Social"


def test_a_row_with_no_data_event_attribute_at_all_is_counted_too():
    rows = row_html("ev-one-20260901180000", "Open Shop") + (
        f'<tr data-hook="{AGENDA_ROW_HOOK}"><td><button>Ghost</button></td></tr>'
    )
    result = parse_text(agenda_page(rows))

    assert result.ok
    assert result.row_count == 2
    assert result.bad_row_count == 1
    assert titles(result) == ["Open Shop"]


def test_rows_with_no_usable_data_event_at_all_are_a_reported_schema_change():
    """Twenty rows and not one date is not a quiet week — the attribute was
    renamed or reformatted, and every event on the page is invisible."""
    page = load("sequoiafabrica-agenda.html").replace('data-event="ev-', 'data-entry="ev-')
    result = parse_text(page)

    assert not result.ok
    assert result.problem is BookwhenProblem.NO_DATES
    assert result.events == ()
    assert result.row_count == ROWS_ON_PAGE
    assert result.bad_row_count == ROWS_ON_PAGE
    assert "schema change" in (result.error or "")


# --------------------------------------------------------------------------- empty vs broken


def test_a_page_with_no_rows_is_a_reported_failure():
    """A Bookwhen page whose agenda rows are gone: the markup drifted or the
    list moved behind JavaScript. Both want a human. Neither is a calendar with
    nothing in it."""
    result = parse_text(load("agenda-no-rows.html"))

    assert not result.ok
    assert result.problem is BookwhenProblem.NO_ROWS
    assert result.events == ()
    assert result.reported_empty is False
    assert "agenda_list" in (result.error or "")  # the hooks it did find


def test_a_legitimately_empty_calendar_is_a_clean_zero_and_says_so():
    """The other half of the same coin: the agenda rendered its empty state, so
    the zero is one the page declared and issue 0016's ``allow_zero`` owns what
    to do about it."""
    result = parse_text(load("agenda-empty.html"))

    assert result.ok
    assert result.problem is BookwhenProblem.NONE
    assert result.events == ()
    assert result.reported_empty is True
    assert result.empty_hooks_seen == ("agenda_list_empty",)
    assert "not a parse failure" in (result.error or "")


def test_empty_and_broken_are_distinguishable_from_each_other_and_from_a_stranger():
    """Three pages, three answers, none of them a bare empty list."""
    empty = parse_text(load("agenda-empty.html"))
    broken = parse_text(load("agenda-no-rows.html"))
    stranger = parse_text(load("not-bookwhen.html"))

    assert [empty.ok, broken.ok, stranger.ok] == [True, False, False]
    assert [empty.problem, broken.problem, stranger.problem] == [
        BookwhenProblem.NONE,
        BookwhenProblem.NO_ROWS,
        BookwhenProblem.NOT_BOOKWHEN,
    ]
    assert [empty.reported_empty, broken.reported_empty, stranger.reported_empty] == [
        True,
        False,
        False,
    ]
    # All three carry zero events. Only one of them means "no classes".
    assert (empty.events, broken.events, stranger.events) == ((), (), ())


def test_every_documented_empty_marker_is_believed():
    for hook in EMPTY_HOOKS:
        page = agenda_page("", extra=f'<p data-hook="{hook}">Nothing on</p>')
        result = parse_text(page)

        assert result.ok, hook
        assert result.reported_empty is True, hook
        assert result.empty_hooks_seen == (hook,), hook


# --------------------------------------------------------------------------- HTTP 200 is not success


def test_a_200_with_an_unrelated_html_page_is_reported_not_read_as_empty():
    """**The check that stops a wrong URL looking like a quiet space.**

    A 200, ``text/html``, well-formed, and not an agenda. The live shape of this
    is ``events.sequoiafabrica.org``: every path on it — ``/events.ics``,
    ``/calendar.ics``, ``/ical``, ``/feed``, ``/feed.xml``, ``/rss``,
    ``/index.xml``, ``/sitemap.xml``, ``/robots.txt`` — answers 200 as a redirect
    to Bookwhen. Today that lands on the real agenda; the day it lands anywhere
    else, this is what the adapter sees.
    """
    result = parse_agenda_from(load("not-bookwhen.html"))

    assert not result.ok
    assert result.problem is BookwhenProblem.NOT_BOOKWHEN
    assert result.events == ()
    assert "not a Bookwhen agenda page" in (result.error or "")
    # It mentions Bookwhen by name in a link — branding is not a signal.
    assert "bookwhen.com" in load("not-bookwhen.html")
    assert looks_like_bookwhen(load("not-bookwhen.html")) is False


def parse_agenda_from(document: str) -> BookwhenParse:
    return parse_bookwhen_html(fetched(document), today=TODAY, now=NOW)


def test_a_page_is_recognized_by_bookwhens_own_markup_and_not_by_its_name():
    assert looks_like_bookwhen(load("sequoiafabrica-agenda.html")) is True
    assert looks_like_bookwhen(load("agenda-empty.html")) is True
    assert looks_like_bookwhen(load("agenda-no-rows.html")) is True
    # An agenda hook alone is enough, with no data-options at all…
    assert looks_like_bookwhen(agenda_page("", options=False)) is True
    # …and so is the data-options calendar block with no agenda hook.
    assert looks_like_bookwhen(
        '<html><body><div data-options=\'{"calendar":"abc"}\'></div></body></html>'
    ) is True
    assert looks_like_bookwhen("<html><body><p>hello</p></body></html>") is False


def test_a_non_html_body_is_refused_before_it_is_ever_parsed():
    result = parse_bookwhen_html(
        fetched(b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n", content_type="text/calendar"),
        today=TODAY,
        now=NOW,
    )

    assert result.problem is BookwhenProblem.WRONG_CONTENT_TYPE
    assert "HTTP 200 is not success" in (result.error or "")


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"status_code": 404}, BookwhenProblem.HTTP_ERROR),
        ({"status_code": 503}, BookwhenProblem.HTTP_ERROR),
        ({"body": b"", "status_code": 200}, BookwhenProblem.EMPTY_BODY),
        ({"outcome": Outcome.NOT_MODIFIED}, BookwhenProblem.NOT_MODIFIED),
        ({"outcome": Outcome.FAILED, "reason": "connect timeout"}, BookwhenProblem.TRANSPORT),
        ({"outcome": Outcome.BLOCKED, "reason": "robots.txt"}, BookwhenProblem.TRANSPORT),
    ],
)
def test_transport_and_status_failures_never_look_like_an_empty_agenda(
    kwargs: dict[str, Any], expected: BookwhenProblem
):
    body = kwargs.pop("body", load("sequoiafabrica-agenda.html"))
    result = parse_bookwhen_html(fetched(body, **kwargs), today=TODAY, now=NOW)

    assert result.problem is expected
    assert not result.ok
    assert result.events == ()


# --------------------------------------------------------------------------- horizon


def test_the_horizon_clips_on_the_events_own_local_day():
    """20 rows, a 120-day window opening 2026-08-05, and 16 survivors. The four
    dropped rows are 2026-12-08 … 2027-01-05 — real events, published later."""
    result = parse_agenda()

    assert result.window_start == TODAY
    assert result.window_end == HORIZON_END
    assert result.row_count == ROWS_ON_PAGE  # raw_count: what the page carried
    assert result.event_count == EVENTS_IN_HORIZON  # the health-gate number
    assert max(event.start.date() for event in result.events) == dt.date(2026, 12, 1)
    assert result.last_row_start == dt.date(2027, 1, 5)


def test_a_wider_horizon_keeps_every_row_on_the_page():
    result = parse_agenda(horizon_days=200)

    assert result.event_count == ROWS_ON_PAGE
    assert max(event.start.date() for event in result.events) == dt.date(2027, 1, 5)


def test_events_are_sorted_by_their_start():
    result = parse_agenda(horizon_days=200)

    assert starts(result) == sorted(starts(result))


def test_the_window_is_inclusive_at_both_ends():
    page = agenda_page(
        row_html("ev-first-20260805090000", "Opening day")
        + row_html("ev-last-20261203230000", "Closing day")
        + row_html("ev-past-20260804090000", "Yesterday")
        + row_html("ev-far-20261204090000", "One day too far")
    )
    result = parse_text(page)

    assert titles(result) == ["Opening day", "Closing day"]
    assert result.row_count == 4


# --------------------------------------------------------------------------- UIDs


def test_uids_are_the_entry_id_and_the_start_and_are_stable_across_parses():
    """**UID stability is the invariant that breaks quietly.** If these churn,
    every subscriber sees every event as new every night."""
    first = parse_agenda()
    second = parse_agenda(now=NOW + dt.timedelta(hours=19))

    assert [event.uid for event in first.events] == [event.uid for event in second.events]
    assert first.events[0].uid == "sfhandemb1:20260813180000"
    # Nothing in a UID but the source's own key and the source's own stamp — no
    # scrape timestamp, no page position, no title.
    assert all(
        event.uid == f"{event.entry_id}:{event.start_stamp}"  # type: ignore[attr-defined]
        for event in first.events
    )
    assert all(re.fullmatch(r"[A-Za-z0-9_-]+:\d{14}", event.uid or "") for event in first.events)


def test_eleven_orientations_sharing_one_title_get_eleven_distinct_uids():
    result = parse_agenda(horizon_days=200)
    orientations = [
        event for event in result.events if event.title == "Member Applicant Orientation"
    ]

    assert len(orientations) == 11
    assert len({event.uid for event in orientations}) == 11
    assert len({event.uid for event in result.events}) == ROWS_ON_PAGE


def test_the_uid_uses_the_stamp_the_page_wrote_not_a_computed_instant():
    """The stamp is the source's own text, so the UID cannot move if our
    timezone handling ever changes. Recurrence is not a factor: Bookwhen gives
    each occurrence its own entry, so ``recurring`` stays False and the
    occurrence start is already in the key."""
    event = by_stamp(parse_agenda(), "20260818183000")

    assert event.entry_id == "sfmao01"
    assert event.start_stamp == "20260818183000"
    assert event.uid == "sfmao01:20260818183000"
    assert event.recurring is False


def test_normalize_namespaces_the_uid_and_keeps_the_wall_clock(registry: Registry):
    space = registry.space(SPACE_ID)
    normalization = normalize_ics(
        parse_agenda(), space=space, source_label=LABEL, now=NOW
    )
    events = list(normalization.events)

    assert len(events) == EVENTS_IN_HORIZON
    assert not normalization.dropped
    assert not normalization.quarantined
    first = events[0]
    assert first.uid == f"{SPACE_ID}:sfhandemb1:20260813180000"
    assert first.start_utc == dt.datetime(2026, 8, 14, 1, 0, tzinfo=dt.timezone.utc)
    assert first.tz == "America/Los_Angeles"
    assert first.url == f"{AGENDA_URL}/e/ev-sfhandemb1-20260813180000"


# --------------------------------------------------------------------------- links and rooms


def test_the_booking_link_is_resolved_against_the_page_url():
    """Every event links back to the source — CLAUDE.md's rule — and the row's
    own ``href`` is relative."""
    event = parse_agenda().events[0]

    assert event.url == f"{AGENDA_URL}/e/ev-sfhandemb1-20260813180000"


def test_a_row_with_no_link_leaves_the_url_alone():
    result = parse_text(agenda_page(row_html("ev-one-20260901180000", "Open Shop")))

    assert result.events[0].url is None


def test_a_location_cell_is_read_when_one_exists_and_is_none_when_it_does_not():
    """Sequoia Fabrica's rows carry none, so ``VenuePolicy`` applies the space
    record. An account that does render one gets it — a room name is evidence
    the policy can act on."""
    with_room = parse_text(
        agenda_page(
            row_html("ev-one-20260901180000", "Open Shop", location="Textiles Grove")
        )
    )

    assert with_room.events[0].location == "Textiles Grove"
    assert all(event.location is None for event in parse_agenda().events)


# --------------------------------------------------------------------------- pagination


def test_calendar_items_url_is_built_from_the_page_not_guessed():
    """The slug comes from the registered URL and the ``calendar`` id from the
    page's own ``data-options``. Nothing here is invented."""
    url = calendar_items_url(AGENDA_URL, offset=20, limit=50, calendar="lcqebfpp6u7h")

    assert url == (
        "https://bookwhen.com/sequoiafabrica/calendar_items"
        "?offset=20&limit=50&calendar=lcqebfpp6u7h&context=api"
    )
    assert calendar_items_url("https://bookwhen.com/") is None


def test_the_calendar_items_response_is_javascript_and_has_to_be_unescaped():
    """It answers ``text/javascript`` with jQuery statements wrapping escaped
    HTML, despite ``context=api``. Not JSON. This is the single reason
    pagination is opt-in rather than the default path."""
    body = load("calendar-items.js")
    assert "$(" in body
    assert '\\"' in body

    recovered = html_from_calendar_items(body)

    assert recovered.startswith("<tr ")
    assert '\\"' not in recovered
    rows = agenda_rows(recovered)
    assert len(rows) == 2
    assert rows[1].title == "Crochet & Knitting Social"
    assert rows[1].start_local == dt.datetime(2026, 11, 24, 18, 0)
    # The unrelated statements in the same body are not rows and are ignored.
    assert "Showing 22 of 22 events" not in recovered


def test_a_paginated_window_overlapping_the_default_page_collapses_the_repeat():
    result = parse_text(
        load("sequoiafabrica-agenda.html"),
        extra_documents=[html_from_calendar_items(load("calendar-items.js"))],
    )

    assert result.ok
    assert result.page_count == 2
    assert result.row_count == ROWS_ON_PAGE + 2
    assert result.duplicate_row_count == 1
    assert result.event_count == EVENTS_IN_HORIZON + 1
    assert len({event.uid for event in result.events}) == result.event_count
    assert "overlapping the default page" in (result.error or "")


def test_a_page_that_runs_out_before_the_horizon_is_the_one_that_wants_more():
    """``looks_paginated`` is the whole trigger, and it reads the **last row**
    rather than the last surviving event — clipping guarantees the latter is
    inside the window whether the page ran short or not."""
    short = agenda_page(
        "".join(
            row_html(
                f"ev-row{index:02d}-{(dt.date(2026, 8, 13) + dt.timedelta(days=3 * index)):%Y%m%d}180000",
                f"Class {index}",
            )
            for index in range(DEFAULT_PAGE_SIZE)
        )
    )
    result = parse_text(short)

    assert result.row_count == DEFAULT_PAGE_SIZE
    assert result.last_row_start == dt.date(2026, 10, 9)
    assert result.last_row_start < HORIZON_END
    assert result.looks_paginated is True
    # The real page does not trip it.
    assert parse_agenda().looks_paginated is False


# --------------------------------------------------------------------------- the CLI seam


def test_parse_is_the_registry_entry_point():
    assert parse is parse_bookwhen_html


def test_the_dispatch_table_wires_the_adapter_with_no_extra_appetites():
    """One request, no registry configuration. The row hook and the
    ``data-event`` format are what make this adapter Bookwhen's, so it needs
    neither the ``Fetcher`` nor the ``SourceRef`` — the ``nextdata`` shape, not
    the ``embedded_json`` one."""
    entry = ADAPTERS["bookwhen_html"]

    assert entry.implemented
    assert entry.parse is parse_bookwhen_html
    assert entry.issue == "0024"
    assert entry.paginates is False
    assert entry.needs_source is False
    assert "bookwhen_html" in implemented_adapters()


def test_the_registered_source_is_runnable_now(agenda_ref: SourceRef):
    assert agenda_ref.source.url == AGENDA_URL
    assert agenda_ref.source.verified is True
    assert is_runnable(agenda_ref)


def test_this_source_is_a_fallback_and_the_registry_says_so(registry: Registry):
    """Issue 0002 is chasing the Bookwhen ICS token. When it lands, that feed —
    already registered at ``trust: 100`` with ``url: TODO`` — supersedes this
    one, and this source is demoted or dropped."""
    space = registry.space(SPACE_ID)
    ics_source = next(s for s in space.sources if s.adapter == "ics")
    html_source = next(s for s in space.sources if s.adapter == "bookwhen_html")

    assert ics_source.is_todo
    assert ics_source.trust > html_source.trust
    assert "fallback" in (html_source.notes or "").lower()


def test_fetch_bookwhen_html_drives_the_whole_flow_in_exactly_one_request(
    registry: Registry, agenda_ref: SourceRef, tmp_path: Path
):
    """One polite GET of one public page. ``calendar_items`` is not touched:
    the 20 rows already run past the horizon."""
    router = Router({"/sequoiafabrica": html_response(load("sequoiafabrica-agenda.html"))})
    fetcher = make_fetcher(registry, router, tmp_path)

    result = fetch_bookwhen_html(fetcher, agenda_ref, today=TODAY, now=NOW, horizon_days=120)
    fetcher.close()

    assert result.ok
    assert result.event_count == EVENTS_IN_HORIZON
    assert router.urls == [AGENDA_URL]


def test_paginate_true_still_makes_one_request_when_the_page_already_reaches(
    registry: Registry, agenda_ref: SourceRef, tmp_path: Path
):
    router = Router({"/sequoiafabrica": html_response(load("sequoiafabrica-agenda.html"))})
    fetcher = make_fetcher(registry, router, tmp_path)

    result = fetch_bookwhen_html(
        fetcher, agenda_ref, today=TODAY, now=NOW, horizon_days=120, paginate=True
    )
    fetcher.close()

    assert result.event_count == EVENTS_IN_HORIZON
    assert router.urls == [AGENDA_URL]
    assert "calendar_items" not in " ".join(router.urls)


def test_paginate_true_makes_exactly_one_more_request_when_the_page_runs_short(
    registry: Registry, agenda_ref: SourceRef, tmp_path: Path
):
    """The deliberate second step. Same rate limiter, same robots decision, same
    ``raw/`` archive as the first request."""
    short = agenda_page(
        "".join(
            row_html(
                f"ev-row{index:02d}-{(dt.date(2026, 8, 13) + dt.timedelta(days=3 * index)):%Y%m%d}180000",
                f"Class {index}",
            )
            for index in range(DEFAULT_PAGE_SIZE)
        )
    )
    more = (
        '$("#agenda_list").append("'
        + row_html("ev-extra-20261110180000", "Late addition")
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        + '");'
    )
    router = Router(
        {
            "calendar_items": js_response(more),
            "/sequoiafabrica": html_response(short),
        }
    )
    fetcher = make_fetcher(registry, router, tmp_path)

    result = fetch_bookwhen_html(
        fetcher, agenda_ref, today=TODAY, now=NOW, horizon_days=120, paginate=True
    )
    fetcher.close()

    assert len(router.urls) == 2
    assert router.urls[0] == AGENDA_URL
    assert router.urls[1] == (
        "https://bookwhen.com/sequoiafabrica/calendar_items"
        "?offset=20&limit=50&calendar=lcqebfpp6u7h&context=api"
    )
    assert result.page_count == 2
    assert result.row_count == DEFAULT_PAGE_SIZE + 1
    assert "Late addition" in titles(result)


def test_a_failed_second_page_never_costs_the_first(
    registry: Registry, agenda_ref: SourceRef, tmp_path: Path
):
    short = agenda_page(
        "".join(
            row_html(
                f"ev-row{index:02d}-{(dt.date(2026, 8, 13) + dt.timedelta(days=3 * index)):%Y%m%d}180000",
                f"Class {index}",
            )
            for index in range(DEFAULT_PAGE_SIZE)
        )
    )
    router = Router(
        {
            "calendar_items": httpx.Response(503, content=b""),
            "/sequoiafabrica": html_response(short),
        }
    )
    fetcher = make_fetcher(registry, router, tmp_path)

    result = fetch_bookwhen_html(
        fetcher, agenda_ref, today=TODAY, now=NOW, horizon_days=120, paginate=True
    )
    fetcher.close()

    assert result.ok
    assert result.event_count == DEFAULT_PAGE_SIZE
    assert result.page_count == 1


def test_process_source_publishes_the_agenda(
    registry: Registry, agenda_ref: SourceRef, tmp_path: Path
):
    router = Router({"/sequoiafabrica": html_response(load("sequoiafabrica-agenda.html"))})
    fetcher = make_fetcher(registry, router, tmp_path)

    record, events = process_source(agenda_ref, fetcher, horizon_days=120, now=NOW)
    fetcher.close()

    assert record.status == "ok"
    assert record.problem == "none"
    assert record.raw_count == ROWS_ON_PAGE  # rows on the page
    assert record.horizon_count == EVENTS_IN_HORIZON  # the health-gate number
    assert record.event_count == EVENTS_IN_HORIZON
    assert len(events) == EVENTS_IN_HORIZON
    assert all(event.tz == "America/Los_Angeles" for event in events)
    assert all(event.url and event.url.startswith(AGENDA_URL) for event in events)


def test_an_unrelated_page_fails_the_source_rather_than_emptying_it(
    registry: Registry, agenda_ref: SourceRef, tmp_path: Path
):
    """The end-to-end version of the check that matters most: a 200 with the
    wrong page in it must make the source *fail*, so carry-forward republishes
    yesterday's events instead of the calendar losing a space."""
    router = Router({"/sequoiafabrica": html_response(load("not-bookwhen.html"))})
    fetcher = make_fetcher(registry, router, tmp_path)

    record, events = process_source(agenda_ref, fetcher, horizon_days=120, now=NOW)
    fetcher.close()

    assert record.status == "failed"
    assert record.problem == "not_bookwhen"
    assert events == []
