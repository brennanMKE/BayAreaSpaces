"""Tests for the Google Calendar adapter (issue 0008).

**No test here touches the network.** Issue 0001 — the bot about page that
``$MAKER_CALENDAR_CONTACT`` points at — is still open, so a live request would go
out under a User-Agent naming a page that does not resolve. Every request in this
file goes through ``httpx.MockTransport``, and every payload is a hand-authored
fixture under ``tests/fixtures/gcal/``.

``today`` is pinned to **2026-08-05**, the source-survey date, so the horizon
counts below are exact rather than approximate.

Three things are being defended.

**One calendar id, one URL.** The id appears in this project in two spellings —
``sources.yaml`` writes a literal ``@``, ``references/feeds.json`` and
``spaces/*.md`` write ``%40`` — and both must produce a byte-identical request.
Encoding an already-encoded id yields ``%2540``, which is a *valid request for a
calendar that does not exist*: Google answers 404 and the space vanishes from the
calendar without anything erroring.

**The feed's own metadata reaches the caller.** ``LAST-MODIFIED`` is what issue
0016's ``max_stale_days`` gate runs on, and ``X-WR-CALDESC`` is what proved
Sequoia Fabrica's gCal is disjoint from its Bookwhen feed. ``X-WR-CALNAME`` is
surfaced too and is never a label — Maker Nexus's calendar is named "Amilia
Published Classes" and Humanmade's is named "Personal".

**A 404 is a reported failure.** It is the negative control this registry's ids
were verified with, and it must never look like a space with no events on.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import httpx
import pytest

from pipeline.adapters.gcal_ics import (
    GCAL_ICS_TEMPLATE,
    InvalidCalendarId,
    calendar_ics_url,
    encode_calendar_id,
    fetch_gcal_ics,
    normalize_calendar_id,
    parse,
    parse_gcal_ics,
    parse_gcal_ics_text,
    source_ics_url,
)
from pipeline.adapters.ics import IcsProblem
from pipeline.config import Registry, Source, load_registry
from pipeline.fetch import FetchResult, Fetcher, Outcome, resolve_url

FIXTURES = Path(__file__).parent / "fixtures" / "gcal"

#: The source-survey date. Every fixture is authored relative to it.
TODAY = dt.date(2026, 8, 5)
NOW = dt.datetime(2026, 8, 5, 3, 15, tzinfo=dt.timezone.utc)
HORIZON_END = TODAY + dt.timedelta(days=120)

TEST_CONTACT = "https://maker-calendar.test/about"
USER_AGENT = f"bayarea-maker-calendar/0.1 (+{TEST_CONTACT})"

#: The two real ids in ``sources.yaml``, and the ``%40`` spelling of the first as
#: it appears in ``references/feeds.json`` and ``spaces/sequoia-fabrica.md``.
SEQUOIA_ID = "c_69d095340ce714f6a0769a561fa4414c07981195eb1c9be7fde47a5cdd5450a5@group.calendar.google.com"
SEQUOIA_ID_ENCODED = SEQUOIA_ID.replace("@", "%40")
MAKER_NEXUS_ID = "c_dd13e6622c96fb917233442f8d9b9fc23848858063c36902f15d077b772bbd82@group.calendar.google.com"

SEQUOIA_URL = (
    "https://calendar.google.com/calendar/ical/"
    f"{SEQUOIA_ID_ENCODED}/public/basic.ics"
)


# --------------------------------------------------------------------------- helpers


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def parse_fixture(name: str, **kwargs: object):
    kwargs.setdefault("today", TODAY)
    kwargs.setdefault("now", NOW)
    return parse_gcal_ics_text(load(name), space_id="test-space", label=name, **kwargs)  # type: ignore[arg-type]


def fetched(
    body: str | bytes,
    *,
    content_type: str | None = "text/calendar",
    status_code: int = 200,
    outcome: Outcome = Outcome.FETCHED,
    label: str = "community-calendar",
    space_id: str = "sequoia-fabrica",
    url: str = SEQUOIA_URL,
) -> FetchResult:
    """A :class:`FetchResult` as the fetch layer would hand one to the adapter."""
    payload = body.encode("utf-8") if isinstance(body, str) else body
    return FetchResult(
        space_id=space_id,
        label=label,
        adapter="gcal_ics",
        url=url,
        outcome=outcome,
        status_code=status_code,
        content_type=content_type,
        body=payload,
        byte_count=len(payload),
        charset="utf-8",
    )


class Router:
    """A ``httpx.MockTransport`` handler recording every URL it is asked for."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self._routes: dict[str, tuple[int, bytes, dict[str, str]]] = {}

    def add(
        self,
        url: str,
        status: int = 200,
        body: bytes = b"",
        content_type: str | None = "text/calendar; charset=utf-8",
    ) -> Router:
        headers = {"Content-Type": content_type} if content_type else {}
        self._routes[url] = (status, body, headers)
        return self

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/robots.txt":
            # 404 means "no robots.txt", i.e. everything is allowed.
            return httpx.Response(404)
        route = self._routes.get(str(request.url))
        if route is None:
            raise AssertionError(f"unexpected request to {request.url}")
        status, body, headers = route
        return httpx.Response(status, content=body, headers=headers)

    @property
    def target_urls(self) -> list[str]:
        return [str(r.url) for r in self.requests if r.url.path != "/robots.txt"]


def gcal_registry(*sources: dict) -> Registry:
    return Registry.model_validate(
        {
            "defaults": {
                "timezone": "America/Los_Angeles",
                "user_agent": USER_AGENT,
                "rate_limit_seconds": 2.0,
                "horizon_days": 120,
            },
            "spaces": [
                {
                    "id": "sequoia-fabrica",
                    "name": "Sequoia Fabrica",
                    "city": "San Francisco",
                    "region": "sf",
                    "url": "https://www.sequoiafabrica.org/",
                    "sources": list(sources),
                }
            ],
        }
    )


def make_fetcher(registry: Registry, router: Router, tmp_path: Path) -> Fetcher:
    return Fetcher(
        registry,
        transport=httpx.MockTransport(router),
        raw_dir=tmp_path / "raw",
        sleep=lambda _seconds: None,
        now=lambda: NOW,
    )


# --------------------------------------------------------------------------- the URL


def test_the_url_is_the_documented_google_template():
    assert calendar_ics_url(SEQUOIA_ID) == SEQUOIA_URL
    assert calendar_ics_url(SEQUOIA_ID).startswith(
        "https://calendar.google.com/calendar/ical/"
    )
    assert calendar_ics_url(SEQUOIA_ID).endswith("/public/basic.ics")


def test_a_literal_at_and_a_percent40_id_produce_the_identical_url():
    """``sources.yaml`` writes ``@``; feeds.json and spaces/*.md write ``%40``."""
    assert SEQUOIA_ID != SEQUOIA_ID_ENCODED  # the two spellings really do differ

    assert calendar_ics_url(SEQUOIA_ID) == calendar_ics_url(SEQUOIA_ID_ENCODED)
    assert calendar_ics_url(SEQUOIA_ID) == SEQUOIA_URL


def test_an_already_encoded_id_is_not_double_encoded():
    """``%2540group.calendar.google.com`` is a valid request that 404s forever."""
    url = calendar_ics_url(SEQUOIA_ID_ENCODED)

    assert "%2540" not in url
    assert "%25" not in url
    assert url.count("%40") == 1
    assert "@" not in url


def test_encoding_is_idempotent():
    once = encode_calendar_id(SEQUOIA_ID)
    assert encode_calendar_id(once) == once
    assert encode_calendar_id(encode_calendar_id(once)) == once


def test_normalize_collapses_both_spellings_to_the_decoded_id():
    assert normalize_calendar_id(SEQUOIA_ID) == SEQUOIA_ID
    assert normalize_calendar_id(SEQUOIA_ID_ENCODED) == SEQUOIA_ID
    assert normalize_calendar_id(f"  {SEQUOIA_ID}  ") == SEQUOIA_ID


def test_a_calendar_id_that_is_really_a_url_is_rejected():
    """Encoding a whole URL into a path segment gives a plausible 404, not an error."""
    with pytest.raises(InvalidCalendarId) as excinfo:
        calendar_ics_url(SEQUOIA_URL)
    assert "URL" in str(excinfo.value)


@pytest.mark.parametrize("bad", ["", "   ", "not an id", "some/path"])
def test_unusable_calendar_ids_raise_rather_than_building_a_wrong_url(bad):
    with pytest.raises(InvalidCalendarId):
        calendar_ics_url(bad)


def test_the_template_is_the_one_the_fetch_layer_publishes():
    assert GCAL_ICS_TEMPLATE.format(calendar_id=SEQUOIA_ID_ENCODED) == SEQUOIA_URL


def test_the_fetch_layer_and_the_adapter_agree_on_the_url():
    """One implementation, so a `%40` id cannot become `%2540` on one path only."""
    for calendar_id in (SEQUOIA_ID, SEQUOIA_ID_ENCODED):
        source = Source(
            adapter="gcal_ics", calendar_id=calendar_id, label="community-calendar"
        )
        assert resolve_url(source) == calendar_ics_url(calendar_id) == SEQUOIA_URL
        assert source_ics_url(source) == SEQUOIA_URL


def test_every_registered_gcal_source_builds_a_well_formed_url():
    registry = load_registry(env={"MAKER_CALENDAR_CONTACT": TEST_CONTACT})
    gcal = [ref for ref in registry.all_sources if ref.source.adapter == "gcal_ics"]

    assert len(gcal) >= 2, "registry should still carry the gcal_ics sources"
    for ref in gcal:
        url = source_ics_url(ref.source)
        assert url.count("%40") == 1
        assert "%2540" not in url and "@" not in url
        assert url == resolve_url(ref.source)

    labels = {ref.source.label for ref in gcal}
    assert {"community-calendar", "amilia-published-classes"} <= labels
    # noisebridge-today stays disabled: dead since 2024-01, five UNTIL-less RRULEs.
    disabled = [ref for ref in gcal if not ref.source.enabled]
    assert [ref.source.label for ref in disabled] == ["noisebridge-today"]


# --------------------------------------------------------------------------- metadata


def test_calname_caldesc_and_last_modified_are_surfaced():
    result = parse_fixture("sequoia-community-calendar.ics")

    assert result.ok
    assert result.calendar_name == "Sequoia Fabrica - Community Calendar"
    assert result.calendar_description == (
        "For member and volunteer events (separate from classes/Bookwhen events)"
    )
    # The line that proved this calendar is disjoint from the Bookwhen feed.
    assert "Bookwhen" in result.calendar_description
    assert result.calendar_timezone == "America/Los_Angeles"

    # LAST-MODIFIED is what issue 0016's max_stale_days gate runs on.
    assert result.last_modified == dt.datetime(2026, 8, 1, 19, 51, 25, tzinfo=dt.timezone.utc)
    assert result.last_change == result.last_modified
    assert result.stale_days == pytest.approx(3.3, abs=0.05)


def test_a_calendar_without_a_caldesc_still_reports_its_name():
    result = parse_fixture("maker-nexus-classes.ics")

    assert result.calendar_name == "Amilia Published Classes"
    assert result.calendar_description is None
    assert result.last_modified == dt.datetime(2026, 8, 4, 16, 45, tzinfo=dt.timezone.utc)


def test_calname_is_never_used_as_the_source_label():
    """Humanmade's Luma calendar is named "Personal". The registry wins."""
    result = parse_gcal_ics(
        fetched(
            load("maker-nexus-classes.ics"),
            space_id="maker-nexus",
            label="amilia-published-classes",
        ),
        today=TODAY,
        now=NOW,
    )

    assert result.calendar_name == "Amilia Published Classes"
    assert result.label == "amilia-published-classes"
    assert result.label != result.calendar_name
    assert result.space_id == "maker-nexus"


# --------------------------------------------------------------------------- delegation


def test_delegation_returns_horizon_clipped_events():
    """89 VEVENTs and ~7 live is the real shape; the fixture is that in miniature."""
    result = parse_fixture("sequoia-community-calendar.ics")

    assert result.ok
    assert result.vevent_count == 4  # incl. one dated 2023-12, outside the horizon
    assert result.recurring_vevent_count == 2  # both monthly, neither with an UNTIL
    assert result.event_count == 8  # post-expansion, inside the window

    starts = [e.start.astimezone(dt.timezone.utc).date() for e in result.events]
    assert all(TODAY <= day <= HORIZON_END for day in starts)
    assert starts == sorted(starts)
    assert dt.date(2023, 12, 16) not in starts

    # Mixed DTSTART forms in one feed, which this calendar genuinely does.
    assert {e.dtstart_form for e in result.events} == {"utc", "tzid"}


def test_pre_expanded_feeds_are_clipped_at_both_ends():
    """Maker Nexus: no RRULEs, history to 2023-12, and a short publishing horizon."""
    result = parse_fixture("maker-nexus-classes.ics")

    assert result.ok
    assert result.vevent_count == 5
    assert result.recurring_vevent_count == 0
    assert result.event_count == 3

    titles = [e.title for e in result.events]
    assert titles == ["Laser Cutter Basics", "Board Game Night", "Intro to CNC Routing"]
    assert "Woodshop Safety (archive)" not in titles  # 2023-12, before the window
    assert "Beyond The Horizon Class" not in titles  # 2027-01, past the window


def test_parse_is_the_registry_entry_point():
    assert parse is parse_gcal_ics
    result = parse(fetched(load("sequoia-community-calendar.ics")), today=TODAY, now=NOW)
    assert result.ok and result.event_count == 8


# --------------------------------------------------------------------------- failures


def test_a_404_is_a_reported_failure_not_an_empty_success():
    """The negative control: a bogus calendar id 404s with Google's HTML page."""
    result = parse_gcal_ics(
        fetched(load("not-found.html"), content_type="text/html", status_code=404),
        today=TODAY,
        now=NOW,
    )

    assert result.ok is False
    assert bool(result) is False  # falsy despite holding zero events
    assert result.event_count == 0
    assert result.problem is IcsProblem.NOT_CALENDAR
    assert "404" in result.error
    assert "negative control" in result.error
    assert result.window_start == TODAY and result.window_end == HORIZON_END


def test_a_200_with_an_html_body_is_still_reported():
    result = parse_gcal_ics(
        fetched(load("not-found.html"), content_type="text/html", status_code=200),
        today=TODAY,
        now=NOW,
    )

    assert result.ok is False
    assert result.problem is IcsProblem.WRONG_CONTENT_TYPE
    assert result.event_count == 0


def test_a_304_is_not_modified_rather_than_zero():
    result = parse_gcal_ics(
        FetchResult(
            space_id="maker-nexus",
            label="amilia-published-classes",
            adapter="gcal_ics",
            url=SEQUOIA_URL,
            outcome=Outcome.NOT_MODIFIED,
            status_code=304,
        ),
        today=TODAY,
        now=NOW,
    )

    assert result.ok is False
    assert result.problem is IcsProblem.NOT_MODIFIED
    assert "304" in result.error


def test_a_transport_failure_is_not_a_404():
    result = parse_gcal_ics(
        FetchResult(
            space_id="sequoia-fabrica",
            label="community-calendar",
            adapter="gcal_ics",
            url=SEQUOIA_URL,
            outcome=Outcome.FAILED,
            reason="request failed after retries",
        ),
        today=TODAY,
        now=NOW,
    )

    assert result.problem is IcsProblem.TRANSPORT


# --------------------------------------------------------------------------- fetch path


def test_fetch_and_parse_requests_the_encoded_url_and_returns_events(tmp_path):
    registry = gcal_registry(
        {
            "adapter": "gcal_ics",
            "calendar_id": SEQUOIA_ID,
            "label": "community-calendar",
            "verified": True,
        }
    )
    router = Router().add(
        SEQUOIA_URL, body=load("sequoia-community-calendar.ics").encode("utf-8")
    )
    fetcher = make_fetcher(registry, router, tmp_path)
    ref = next(registry.iter_enabled())

    result = fetch_gcal_ics(fetcher, ref, today=TODAY, now=NOW)

    assert router.target_urls == [SEQUOIA_URL]
    assert result.ok
    assert result.event_count == 8
    assert result.label == "community-calendar"
    assert result.calendar_description is not None
    assert result.horizon_days == 120


def test_both_id_spellings_reach_the_same_request_url(tmp_path):
    """The whole point, end to end: two spellings, one request."""
    seen: list[str] = []
    for calendar_id in (SEQUOIA_ID, SEQUOIA_ID_ENCODED):
        registry = gcal_registry(
            {
                "adapter": "gcal_ics",
                "calendar_id": calendar_id,
                "label": "community-calendar",
            }
        )
        router = Router().add(
            SEQUOIA_URL, body=load("sequoia-community-calendar.ics").encode("utf-8")
        )
        fetcher = make_fetcher(registry, router, tmp_path / calendar_id[:12])
        result = fetch_gcal_ics(fetcher, next(registry.iter_enabled()), today=TODAY, now=NOW)

        assert result.ok and result.event_count == 8
        seen.extend(router.target_urls)

    assert seen == [SEQUOIA_URL, SEQUOIA_URL]


def test_a_bogus_calendar_id_404s_through_the_whole_path(tmp_path):
    bogus = "bogus@group.calendar.google.com"
    url = calendar_ics_url(bogus)
    registry = gcal_registry(
        {
            "adapter": "gcal_ics",
            "calendar_id": bogus,
            "label": "community-calendar",
            "verified": False,
        }
    )
    router = Router().add(
        url,
        status=404,
        body=load("not-found.html").encode("utf-8"),
        content_type="text/html; charset=UTF-8",
    )
    fetcher = make_fetcher(registry, router, tmp_path)

    result = fetch_gcal_ics(fetcher, next(registry.iter_enabled()), today=TODAY, now=NOW)

    assert router.target_urls == [url]
    assert result.ok is False
    assert result.problem is IcsProblem.NOT_CALENDAR
    assert "404" in result.error
    assert result.event_count == 0


def test_the_fetch_path_sends_a_conditional_get_for_the_11mb_export(tmp_path):
    """Maker Nexus is 11 MB with history to 2023-12; a 304 is the whole game."""
    from pipeline.fetch import InMemoryStateStore, SourceState

    url = calendar_ics_url(MAKER_NEXUS_ID)
    registry = gcal_registry(
        {
            "adapter": "gcal_ics",
            "calendar_id": MAKER_NEXUS_ID,
            "label": "amilia-published-classes",
        }
    )
    state = InMemoryStateStore(
        {"sequoia-fabrica:amilia-published-classes": SourceState(etag='W/"abc123"')}
    )
    router = Router().add(url, status=304, body=b"", content_type=None)
    fetcher = Fetcher(
        registry,
        transport=httpx.MockTransport(router),
        raw_dir=tmp_path / "raw",
        sleep=lambda _seconds: None,
        now=lambda: NOW,
        state=state,
    )

    result = fetch_gcal_ics(fetcher, next(registry.iter_enabled()), today=TODAY, now=NOW)

    sent = [r for r in router.requests if r.url.path != "/robots.txt"][0]
    assert sent.headers["If-None-Match"] == 'W/"abc123"'
    assert result.problem is IcsProblem.NOT_MODIFIED
    assert result.ok is False  # "unchanged" is not "zero events"
