"""Tests for the RSS/Atom adapter (issue 0025).

**No test here touches the network.** Issue 0001 — the bot about page that
``$MAKER_CALENDAR_CONTACT`` points at — is still open, so a live request would go
out under a User-Agent naming a page that does not resolve. Every request in this
file goes through ``httpx.MockTransport`` and every payload is a hand-authored
fixture under ``tests/fixtures/rss/``.

``today`` is pinned to **2026-08-05**, the source-survey date, so the horizon
counts below are exact rather than approximate.

Six things are being defended.

**The declaration is required and is never inferred.** The same six-item document
yields four events when the registry says ``pubdate_means: event_start`` and
**zero** when it does not. Nothing about the bytes decides it — which is the
whole design, because a post date and an event start are indistinguishable by
inspection and getting it backwards publishes a calendar of announcement dates.

**"No events" is three different statements.** A change signal (``ok``, populated
liveness), a seed list (``ok``, populated links) and a feed that parsed and held
nothing (``NO_ITEMS``, ``ok=False``) are one boolean apart on purpose, because at
09:00 they have three different repairs.

**Both flavours really parse.** RSS 2.0's ``<pubDate>`` in RFC 822 with a real
offset, and Atom's ``<updated>`` in RFC 3339 with the link in an attribute and
the identity in ``<id>``.

**The two 404 traps, asserted as policy rather than as accident.** Ace's
``/calendar/feed/`` (404 over a valid RSS body) is rejected on the *status*, with
the body's item count still in the report; The Crucible's bogus-category feed
(``application/rss+xml`` over 1.35 MB of HTML) is rejected on the *parse result*
at 200 as well as at 404 — the header never wins.

**UIDs are the feed's own ``<guid>``**, unchanged across a re-parse and across a
second document that happens to reorder its items.

**The horizon clips.** A start on 2027-01-01 is outside a 120-day window opened
on 2026-08-05 and does not reach the calendar, while still being counted as an
item that was there.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import httpx
import pytest

from pipeline.adapters.ics import IcsEvent, IcsParse
from pipeline.adapters.jsonld import seed_links
from pipeline.adapters.rss import (
    DEFAULT_MODE,
    DEFAULT_ZONE,
    LENIENT_CONTENT_TYPES,
    PUBDATE_MEANINGS,
    RSS_MODES,
    Feed,
    PubDate,
    RssEvent,
    RssLiveness,
    RssMode,
    RssParse,
    RssProblem,
    body_shape,
    fetch_rss,
    item_links,
    looks_like_feed,
    looks_like_html,
    parse,
    parse_feed,
    parse_feed_date,
    parse_rss,
    parse_rss_text,
)
from pipeline.config import Registry, RegistryError, Source, SourceRef, load_registry
from pipeline.fetch import FetchResult, Fetcher, Outcome
from pipeline.normalize import normalize_ics

FIXTURES = Path(__file__).parent / "fixtures" / "rss"
JSONLD_FIXTURES = Path(__file__).parent / "fixtures" / "jsonld"

#: The source-survey date. Every fixture is authored relative to it.
TODAY = dt.date(2026, 8, 5)
#: The launchd hour, so a feed dated "yesterday evening" reads as fresh.
NOW = dt.datetime(2026, 8, 5, 3, 15, tzinfo=dt.timezone.utc)
PACIFIC_SUMMER = dt.timezone(dt.timedelta(hours=-7))

TEST_CONTACT = "https://maker-calendar.test/about"

SUDO_EVENTS_URL = "https://sudoroom.org/events/feed/"
SUDO_MASTODON_URL = "https://sfba.social/@sudoroom.rss"
CRUCIBLE_SEED_URL = "https://www.thecrucible.org/category/upcoming-events/feed/"
ACE_TRAP_URL = "https://www.acemakerspace.org/calendar/feed/"


# --------------------------------------------------------------------------- helpers


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fetched(
    body: str | bytes,
    *,
    content_type: str | None = "application/rss+xml",
    status_code: int = 200,
    outcome: Outcome = Outcome.FETCHED,
    label: str = "events-rss",
    space_id: str = "sudo-room",
    url: str = SUDO_EVENTS_URL,
    reason: str | None = None,
) -> FetchResult:
    """A :class:`FetchResult` as the fetch layer would hand one to the adapter."""
    payload = body.encode("utf-8") if isinstance(body, str) else body
    return FetchResult(
        space_id=space_id,
        label=label,
        adapter="rss",
        url=url,
        outcome=outcome,
        status_code=status_code,
        content_type=content_type,
        body=payload,
        byte_count=len(payload),
        charset="utf-8",
        reason=reason,
    )


def sudo_events(**kwargs: Any) -> RssParse:
    """Sudo Room's ``/events/feed/`` read the way the registry declares it."""
    kwargs.setdefault("pubdate_means", "event_start")
    kwargs.setdefault("mode", "events")
    kwargs.setdefault("today", TODAY)
    kwargs.setdefault("now", NOW)
    kwargs.setdefault("source_url", SUDO_EVENTS_URL)
    return parse_rss_text(load("sudoroom-events.rss"), **kwargs)


def titles(result: RssParse) -> list[str]:
    return [event.title or "" for event in result.events]


def by_title(result: RssParse, needle: str) -> RssEvent:
    for event in result.events:
        if event.title and needle in event.title:
            return event  # type: ignore[return-value]
    raise AssertionError(f"no event titled {needle!r} in {titles(result)}")


class Router:
    """An ``httpx.MockTransport`` handler recording every URL it is asked for."""

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


def rss_response(body: str, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        content=body.encode("utf-8"),
        headers={"Content-Type": "application/rss+xml; charset=UTF-8"},
    )


def noop_sleep(seconds: float) -> None:
    """2 s per host. Not in a test suite."""


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def registry() -> Registry:
    return load_registry(env={"MAKER_CALENDAR_CONTACT": TEST_CONTACT})


@pytest.fixture
def events_ref(registry: Registry) -> SourceRef:
    space = registry.space("sudo-room")
    source = next(s for s in space.sources if s.label == "events-rss")
    return SourceRef(space, source)


@pytest.fixture
def mastodon_ref(registry: Registry) -> SourceRef:
    space = registry.space("sudo-room")
    source = next(s for s in space.sources if s.label == "mastodon")
    return SourceRef(space, source)


@pytest.fixture
def seed_ref(registry: Registry) -> SourceRef:
    space = registry.space("the-crucible")
    source = next(s for s in space.sources if s.label == "public-events-seed")
    return SourceRef(space, source)


def make_fetcher(registry: Registry, router: Router, tmp_path: Path) -> Fetcher:
    return Fetcher(
        registry,
        transport=router.transport(),
        raw_dir=tmp_path / "raw",
        sleep=noop_sleep,
        clock=FakeClock(),
    )


# --------------------------------------------------------------------------- rss 2.0


def test_rss_2_0_parses():
    result = sudo_events()

    assert result.ok
    assert result.problem is RssProblem.NONE
    assert result.flavor == "rss"
    assert result.feed_title == "Sudo Room - Events"
    assert result.item_count == 6
    assert result.dated_item_count == 5
    assert result.undated_item_count == 1
    assert result.vevent_count == 6  # raw items, before clipping


def test_parse_is_the_registry_entry_point():
    """``sources.yaml``'s ``adapter: rss`` resolves to ``parse``."""
    assert parse is parse_rss


def test_the_channel_is_read_as_well_as_the_items():
    feed = parse_feed(load("sudoroom-events.rss"))

    assert feed is not None
    assert isinstance(feed, Feed)
    assert feed.flavor == "rss"
    assert feed.title == "Sudo Room - Events"
    assert feed.link == "https://sudoroom.org/events/"
    assert feed.language == "en-US"
    assert feed.updated == dt.datetime(2026, 8, 4, 23, 12, 44, tzinfo=dt.timezone.utc)
    assert feed.item_count == 6


def test_item_fields_are_read_and_entities_are_decoded():
    feed = parse_feed(load("sudoroom-events.rss"))
    assert feed is not None
    item = feed.items[0]

    assert item.title == "Sudo Mesh Weekly"
    assert item.link == "https://sudoroom.org/events/sudo-mesh-weekly-2026-08-06/"
    # `&#038;` in the guid, decoded by the XML parser rather than by us.
    assert item.guid == "https://sudoroom.org/?post_type=event&p=44101"
    assert item.categories == ("Meetings", "Mesh")
    assert item.author == "sudo"
    assert item.description == "Weekly mesh networking meeting. All welcome."
    assert item.date_tag == "pubdate"


# --------------------------------------------------------------------------- atom


def test_atom_parses():
    """Atom differs from RSS in every part a one-format parser hard-codes."""
    result = parse_rss_text(
        load("sudoroom-wiki.atom"),
        pubdate_means="post_date",
        today=TODAY,
        now=NOW,
    )

    assert result.ok
    assert result.flavor == "atom"
    assert result.feed_title == "sudo room - Recent changes [en]"
    assert result.item_count == 3
    assert result.dated_item_count == 3
    # Change detection by default, so a wiki edit never becomes an event.
    assert result.event_count == 0
    assert result.reported_change_signal is True


def test_atom_reads_the_link_attribute_the_id_and_rfc_3339_dates():
    feed = parse_feed(load("sudoroom-wiki.atom"))
    assert feed is not None
    entry = feed.items[0]

    assert entry.title == "Meeting Notes"
    # href attribute, not element text.
    assert entry.link == "https://sudoroom.org/wiki/Meeting_Notes?diff=41022"
    # <id>, not <guid>.
    assert entry.guid == "https://sudoroom.org/wiki/Meeting_Notes?diff=41022"
    # <published> is preferred over <updated>: an edit is not a new post.
    assert entry.date_tag == "published"
    assert entry.date == dt.datetime(2026, 8, 4, 20, 25, 24, tzinfo=dt.timezone.utc)
    assert entry.categories == ("Meetings",)


def test_an_atom_entry_with_only_updated_still_dates():
    feed = parse_feed(load("sudoroom-wiki.atom"))
    assert feed is not None
    entry = feed.items[2]

    assert entry.title == "Laser"
    assert entry.date_tag == "updated"
    assert entry.date == dt.datetime(
        2026, 5, 31, 11, 4, 19, tzinfo=dt.timezone(dt.timedelta(hours=-7))
    )


def test_a_feed_level_rel_self_link_never_becomes_an_item_link():
    """Atom's ``<link rel="self">`` points back at the feed.

    A seed list containing it would have the consuming adapter re-fetch the feed
    it came from, forever.
    """
    links = item_links(load("sudoroom-wiki.atom"))

    assert "https://sudoroom.org/wiki/Special:RecentChanges?feed=atom" not in links
    assert links == (
        "https://sudoroom.org/wiki/Meeting_Notes?diff=41022",
        "https://sudoroom.org/wiki/Mesh?diff=41019",
        "https://sudoroom.org/wiki/Laser?diff=40877",
    )


# --------------------------------------------------------- the declaration


def test_a_feed_without_the_declaration_emits_no_events_and_reports_liveness():
    """**The defining constraint.**

    The same six items that yield four events under ``pubdate_means:
    event_start`` yield **zero** with no declaration at all — and the result is
    ``ok``, carries a populated liveness signal, and says in words that nobody
    declared what these dates mean.
    """
    undeclared = parse_rss_text(load("sudoroom-events.rss"), today=TODAY, now=NOW)

    assert undeclared.ok
    assert undeclared.declared is False
    assert undeclared.mode is DEFAULT_MODE is RssMode.CHANGE_DETECTION
    assert undeclared.pubdate_means is PubDate.POST_DATE
    assert undeclared.event_count == 0
    assert undeclared.emits_events is False

    # The liveness signal, populated. This is what makes it a *report* rather
    # than an empty calendar.
    assert undeclared.reported_change_signal is True
    assert isinstance(undeclared.liveness, RssLiveness)
    assert undeclared.liveness.item_count == 6
    assert undeclared.liveness.digest
    assert "pubdate_means" in (undeclared.error or "")
    assert "no events" in (undeclared.error or "")

    # And the same document, declared, is a calendar.
    declared = sudo_events()
    assert declared.declared is True
    assert declared.event_count == 4


def test_a_change_signal_is_not_the_same_as_parsed_and_found_nothing():
    """One boolean apart, and they must never be confused.

    A feed with items and no event mode is ``ok`` with a liveness object. A feed
    that parsed and carried **zero** items is ``NO_ITEMS`` and ``ok=False`` — a
    repair, not a quiet week.
    """
    signal = parse_rss_text(
        load("sudoroom-mastodon.rss"), pubdate_means="post_date", today=TODAY, now=NOW
    )
    nothing = parse_rss_text(
        load("empty-channel.rss"), pubdate_means="post_date", today=TODAY, now=NOW
    )

    assert signal.ok is True
    assert signal.event_count == 0
    assert signal.reported_change_signal is True
    assert signal.liveness is not None

    assert nothing.ok is False
    assert nothing.problem is RssProblem.NO_ITEMS
    assert nothing.event_count == 0
    assert nothing.reported_change_signal is False
    assert nothing.liveness is None
    assert nothing.flavor == "rss"  # it *was* a feed; it just held nothing
    assert "zero" in (nothing.error or "")


def test_the_liveness_signal_carries_what_a_count_based_gate_cannot_see():
    result = parse_rss_text(
        load("sudoroom-mastodon.rss"), pubdate_means="post_date", today=TODAY, now=NOW
    )
    live = result.liveness
    assert live is not None

    assert live.item_count == 4
    assert live.newest_item_at == dt.datetime(
        2026, 8, 2, 21, 14, 3, tzinfo=dt.timezone.utc
    )
    assert live.oldest_item_at == dt.datetime(
        2026, 7, 20, 19, 30, tzinfo=dt.timezone.utc
    )
    assert live.feed_updated_at == dt.datetime(
        2026, 8, 2, 21, 14, 3, tzinfo=dt.timezone.utc
    )
    assert live.dates_are_post_dates is True
    assert live.age_days is not None and 2 < live.age_days < 3

    payload = live.as_dict()
    assert payload["item_count"] == 4
    assert payload["digest"] == live.digest
    assert payload["dates_are_post_dates"] is True


def test_the_liveness_digest_is_stable_and_moves_only_when_the_feed_does():
    first = parse_rss_text(
        load("sudoroom-mastodon.rss"), pubdate_means="post_date", today=TODAY, now=NOW
    )
    again = parse_rss_text(
        load("sudoroom-mastodon.rss"),
        pubdate_means="post_date",
        today=TODAY,
        # A different observation time must not move it: the digest is over the
        # feed's own item identities and never over a scrape timestamp.
        now=NOW + dt.timedelta(days=3),
    )
    changed = parse_rss_text(
        load("sudoroom-mastodon.rss").replace("113900000000000004", "113900000000000005"),
        pubdate_means="post_date",
        today=TODAY,
        now=NOW,
    )

    assert first.liveness is not None and again.liveness is not None
    assert first.liveness.digest == again.liveness.digest
    assert changed.liveness is not None
    assert changed.liveness.digest != first.liveness.digest


def test_an_undeclared_feed_reports_no_age_rather_than_an_invented_one():
    """"Nobody said what these dates mean" is not the same as "these are fresh"."""
    undeclared = parse_rss_text(load("sudoroom-mastodon.rss"), today=TODAY, now=NOW)

    assert undeclared.liveness is not None
    assert undeclared.liveness.dates_are_post_dates is False
    # The channel's own lastBuildDate is still a legitimate answer.
    assert undeclared.liveness.feed_updated_at is not None
    assert undeclared.last_change == undeclared.liveness.feed_updated_at


def test_a_mastodon_style_feed_emits_no_events():
    """Registered at ``trust: 10`` as a change signal, and that is all it may be.

    Mastodon writes no ``<title>`` and its posts link to ``luma.com`` slugs. The
    toot text can be as event-shaped as it likes; there is no event date in the
    document and this adapter will not manufacture one.
    """
    result = parse_rss_text(
        load("sudoroom-mastodon.rss"),
        pubdate_means="post_date",
        mode="change_detection",
        today=TODAY,
        now=NOW,
    )

    assert result.ok
    assert result.item_count == 4
    assert result.events == ()
    assert result.emits_events is False
    assert result.reported_change_signal is True
    # No <title> anywhere is normal for Mastodon and is not a parse failure.
    feed = parse_feed(load("sudoroom-mastodon.rss"))
    assert feed is not None
    assert all(item.title is None for item in feed.items)
    assert all(item.guid for item in feed.items)


# --------------------------------------------------------------------------- events


def test_a_declared_feed_emits_events_using_pubdate_as_the_start():
    result = sudo_events()

    assert result.ok
    assert result.emits_events is True
    assert titles(result) == [
        "Sudo Mesh Weekly",
        "Machine Learning Reading Group",
        "Omni Commons Delegates Meeting",
        "Open Hack Night",
    ]
    event = by_title(result, "Sudo Mesh Weekly")
    assert event.start == dt.datetime(2026, 8, 6, 18, 0, tzinfo=PACIFIC_SUMMER)
    assert event.pubdate_text == "Thu, 06 Aug 2026 18:00:00 -0700"
    assert event.pubdate_tag == "pubdate"


def test_rfc_822_with_a_timezone_parses_to_the_right_aware_datetime():
    """The offset the feed wrote survives exactly, as an instant."""
    moment = parse_feed_date("Thu, 06 Aug 2026 18:00:00 -0700")

    assert moment.value == dt.datetime(2026, 8, 6, 18, 0, tzinfo=PACIFIC_SUMMER)
    assert moment.value is not None
    assert moment.value.utcoffset() == dt.timedelta(hours=-7)
    assert moment.value.astimezone(dt.timezone.utc) == dt.datetime(
        2026, 8, 7, 1, 0, tzinfo=dt.timezone.utc
    )
    assert moment.form == "offset"
    assert moment.offset == "-07:00"

    # The obsolete named zones RFC 5322 still permits, which these feeds emit.
    assert parse_feed_date("Thu, 06 Aug 2026 18:00:00 PDT").value == dt.datetime(
        2026, 8, 6, 18, 0, tzinfo=PACIFIC_SUMMER
    )
    gmt = parse_feed_date("Tue, 11 Aug 2026 01:00:00 GMT")
    assert gmt.value == dt.datetime(2026, 8, 11, 1, 0, tzinfo=dt.timezone.utc)
    assert gmt.form == "utc"

    # Atom / dc:date, RFC 3339.
    iso = parse_feed_date("2026-08-04T20:25:24Z")
    assert iso.value == dt.datetime(2026, 8, 4, 20, 25, 24, tzinfo=dt.timezone.utc)

    # And nothing at all is an empty moment, never an exception.
    assert parse_feed_date("not a date").value is None
    assert parse_feed_date(None).value is None
    assert parse_feed_date("").value is None


def test_an_aware_event_carries_a_resolvable_iana_zone_not_an_offset():
    """``ZoneInfo`` is called on ``tz`` downstream, and ``ZoneInfo("-07:00")``
    is an error rather than a zone. The instant stays the feed's."""
    event = by_title(sudo_events(), "Sudo Mesh Weekly")

    assert event.tz == DEFAULT_ZONE
    assert event.dtstart_form == "offset"
    assert event.start.utcoffset() == dt.timedelta(hours=-7)


def test_minus_zero_zero_zero_zero_is_passed_on_floating_never_stamped():
    """RFC 5322's ``-0000`` means "local time unknown".

    Guessing a zone here is precisely the silent assumption the no-naive-datetime
    invariant exists to prevent, so it goes on naive and flagged, and
    normalize.py's floating policy — which logs and counts — decides.
    """
    result = sudo_events()
    event = by_title(result, "Open Hack Night")

    assert event.start.tzinfo is None
    assert event.dtstart_form == "floating"
    assert event.tz is None
    assert event.source_tz is None
    assert "-0000" in (result.error or "")


def test_an_item_with_no_date_is_dropped_and_counted_never_assumed():
    result = sudo_events()

    assert "Undated Placeholder" not in titles(result)
    assert result.item_count == 6
    assert result.undated_item_count == 1
    assert "no usable date" in (result.error or "")


def test_an_event_feed_that_stopped_dating_its_items_is_a_reported_failure():
    document = load("sudoroom-events.rss")
    for line in [line for line in document.splitlines() if "<pubDate>" in line]:
        document = document.replace(line, "")

    result = parse_rss_text(
        document, pubdate_means="event_start", mode="events", today=TODAY, now=NOW
    )

    assert result.ok is False
    assert result.problem is RssProblem.NO_DATES
    assert result.item_count == 6
    assert "schema change" in (result.error or "")


def test_events_carry_no_end_and_no_location():
    """The feed has neither, and inventing either is worse than omitting it."""
    event = by_title(sudo_events(), "Sudo Mesh Weekly")

    assert event.end is None
    assert event.location is None  # VenuePolicy applies the address_override
    assert event.recurring is False
    assert event.categories == ("Meetings", "Mesh")
    assert event.organizer == "sudo"


# --------------------------------------------------------------------------- horizon


def test_horizon_clipping_drops_starts_past_the_window_and_still_counts_them():
    result = sudo_events()

    assert result.window_start == TODAY
    assert result.window_end == TODAY + dt.timedelta(days=120)
    # 2027-01-01 is 149 days out.
    assert "New Year Open House" not in titles(result)
    assert result.event_count == 4
    assert result.vevent_count == 6

    wide = sudo_events(horizon_days=200)
    assert "New Year Open House" in titles(wide)
    assert wide.event_count == 5


def test_the_horizon_is_read_from_the_start_not_from_the_publication_date():
    """A narrow horizon keeps only what starts inside it.

    A four-day window opened on 2026-08-05 closes on 08-09, so the two events on
    08-06 survive and the 08-11 delegates meeting does not — clipped on the
    event's own day, not on when anything was published.
    """
    narrow = sudo_events(horizon_days=4)

    assert narrow.window_end == dt.date(2026, 8, 9)
    assert titles(narrow) == [
        "Sudo Mesh Weekly",
        "Machine Learning Reading Group",
    ]
    assert narrow.item_count == 6


# --------------------------------------------------------------------------- uid


def test_uid_is_the_feeds_own_guid_and_is_stable_across_reparses():
    first = sudo_events()
    again = sudo_events(now=NOW + dt.timedelta(days=1), today=TODAY)

    assert [event.uid for event in first.events] == [
        "https://sudoroom.org/?post_type=event&p=44101",
        "https://sudoroom.org/?post_type=event&p=44102",
        "https://sudoroom.org/?post_type=event&p=44103",
        "https://sudoroom.org/?post_type=event&p=44104",
    ]
    assert [event.uid for event in again.events] == [
        event.uid for event in first.events
    ]
    assert all(event.guid == event.uid for event in first.events)


def test_uid_survives_the_feed_reordering_its_items():
    """A UID that moved because a feed re-sorted would re-notify every subscriber."""
    document = load("sudoroom-events.rss")
    start = document.index("    <item>")
    end = document.rindex("</item>") + len("</item>")
    items = [
        block.strip() for block in document[start:end].split("    <item>") if block.strip()
    ]
    reordered = document[:start] + "".join(
        f"    <item>{block}\n" for block in reversed(items)
    ) + document[end:]

    shuffled = parse_rss_text(
        reordered, pubdate_means="event_start", mode="events", today=TODAY, now=NOW
    )
    original = sudo_events()

    assert shuffled.item_count == original.item_count
    # Sorted by start, so the *order* is identical too — but the assertion that
    # matters is that no UID changed.
    assert {event.uid for event in shuffled.events} == {
        event.uid for event in original.events
    }


def test_normalize_namespaces_the_guid_and_keeps_the_instant(registry: Registry):
    """The adapter's output really does travel through ``normalize.py``."""
    space = registry.space("sudo-room")
    normalized = normalize_ics(
        sudo_events(), space=space, source_label="events-rss", now=NOW
    )

    assert normalized.event_count == 4
    mesh = next(
        event for event in normalized.events if event.title == "Sudo Mesh Weekly"
    )
    assert mesh.uid == "sudo-room:https://sudoroom.org/?post_type=event&p=44101"
    assert mesh.start_utc == dt.datetime(2026, 8, 7, 1, 0, tzinfo=dt.timezone.utc)
    # The -0000 item is the one floating time, read by policy and counted.
    assert len(normalized.conversions) == 1
    assert normalized.conversions[0].zone == "America/Los_Angeles"


# --------------------------------------------------------------------------- seed list


def test_the_seed_list_mode_returns_item_link_urls():
    result = parse_rss_text(
        load("crucible-upcoming-events.rss"),
        pubdate_means="post_date",
        mode="seed_list",
        source_url=CRUCIBLE_SEED_URL,
        today=TODAY,
        now=NOW,
    )

    assert result.ok
    assert result.is_seed_list is True
    assert result.item_count == 3
    # No events. The dates in this feed are post dates and the real ones live in
    # the title text, as "(JUL 16)".
    assert result.event_count == 0
    assert result.seed_urls == (
        "https://www.thecrucible.org/event/mosaic-heart-rocks/",
        "https://www.thecrucible.org/event/fire-arts-festival-preview/",
    )
    assert result.seed_count == 2
    assert "no events" in (result.error or "")


def test_a_seed_link_on_another_host_is_refused_with_a_reason():
    """"Fetch only the URLs in ``sources.yaml``" survives a two-step flow only if
    step two stays on the site step one came from."""
    result = parse_rss_text(
        load("crucible-upcoming-events.rss"),
        pubdate_means="post_date",
        mode="seed_list",
        source_url=CRUCIBLE_SEED_URL,
        today=TODAY,
        now=NOW,
    )

    assert len(result.skipped_seeds) == 1
    url, why = result.skipped_seeds[0]
    assert url.startswith("https://www.eventbrite.com/")
    assert "off host" in why
    assert url not in result.seed_urls


def test_a_robots_disallowed_format_route_never_reaches_the_seed_list():
    """``boxshopsf.org/robots.txt`` disallows ``?format=json`` for every agent.

    The check is :func:`pipeline.adapters.jsonld.is_disallowed_route`, reused
    rather than re-implemented, so one place decides which routes are off-limits.
    """
    document = load("crucible-upcoming-events.rss").replace(
        "https://www.thecrucible.org/event/mosaic-heart-rocks/",
        "https://www.thecrucible.org/event/mosaic-heart-rocks/?format=json",
    )
    result = parse_rss_text(
        document,
        pubdate_means="post_date",
        mode="seed_list",
        source_url=CRUCIBLE_SEED_URL,
        today=TODAY,
        now=NOW,
    )

    assert all("format=json" not in url for url in result.seed_urls)
    assert any("robots.txt" in why for _url, why in result.skipped_seeds)


def test_a_seed_feed_with_no_items_is_legitimately_empty_not_a_failure():
    """The Crucible's seed source carries ``allow_zero`` for exactly this."""
    result = parse_rss_text(
        load("empty-channel.rss"),
        pubdate_means="post_date",
        mode="seed_list",
        today=TODAY,
        now=NOW,
    )

    assert result.ok
    assert result.problem is RssProblem.NONE
    assert result.seed_urls == ()
    assert "legitimately empty" in (result.error or "")


def test_the_seed_list_matches_what_jsonld_already_consumes():
    """``jsonld`` follows exactly this list for The Box Shop.

    Two implementations of "which links does a feed offer" would drift, and the
    one that drifted would be the one nobody was reading when a space went quiet.
    This pins them together on the fixture ``jsonld`` itself uses.
    """
    document = (JSONLD_FIXTURES / "boxshop-events.rss").read_text(encoding="utf-8")
    base = "https://boxshopsf.org/events?format=rss"

    assert item_links(document, base_url=base) == seed_links(document, base_url=base)
    assert item_links(document, base_url=base) == (
        "https://boxshopsf.org/events/flg-heavy-pettng-zoo-benefit",
        "https://boxshopsf.org/events/open-studios-preview",
    )

    atom = load("sudoroom-wiki.atom")
    assert item_links(atom) == seed_links(atom)


def test_the_box_shop_seed_feed_never_becomes_a_calendar():
    """Its ``pubDate`` is 2026-06-29 for an event on 2026-08-08.

    Read as events it would publish one wrong date; read as a seed list it
    publishes none and hands the links to ``jsonld``.
    """
    document = (JSONLD_FIXTURES / "boxshop-events.rss").read_text(encoding="utf-8")
    result = parse_rss_text(
        document,
        pubdate_means="post_date",
        mode="seed_list",
        source_url="https://boxshopsf.org/events?format=rss",
        today=TODAY,
        now=NOW,
    )

    assert result.event_count == 0
    assert result.seed_count == 2


# --------------------------------------------------------------------------- trap 1


def test_a_404_with_a_valid_rss_body_is_rejected_on_the_status():
    """**Trap 1, and the documented policy.**

    Ace's ``/calendar/feed/`` answers HTTP 404 with a populated 9.8 KB valid RSS
    body. A body-trusting parser ingests it; a status-checking parser drops it.
    This adapter does neither blindly: it parses the body *first* so the report
    can say what was in it, then fails on the status, and records the
    disagreement as the finding.
    """
    result = parse_rss(
        fetched(
            load("ace-calendar-feed-404.rss"),
            status_code=404,
            space_id="ace-makerspace",
            label="calendar-feed",
            url=ACE_TRAP_URL,
        ),
        pubdate_means="event_start",
        mode="events",
        today=TODAY,
        now=NOW,
    )

    # Rejected — and the body's contents survive into the report.
    assert result.ok is False
    assert result.problem is RssProblem.HTTP_ERROR
    assert result.events == ()
    assert result.http_status == 404
    assert result.body_shape == "rss"
    assert result.item_count == 3
    assert result.disagreements == ("HTTP 404 carrying a valid rss feed with 3 item(s)",)
    assert "Status and body disagree" in (result.error or "")
    assert "404" in (result.error or "")


def test_the_same_body_at_200_would_have_been_a_calendar():
    """The status is doing the work, not the body — which is the whole point.

    Asserting the policy rather than an accident means showing that the body was
    perfectly parseable and was still refused.
    """
    ok = parse_rss(
        fetched(load("ace-calendar-feed-404.rss"), status_code=200, url=ACE_TRAP_URL),
        pubdate_means="event_start",
        mode="events",
        today=TODAY,
        now=NOW,
    )

    assert ok.ok is True
    assert ok.event_count == 3
    assert titles(ok)[0] == "Laser Cutter Basics & Safety"


def test_a_404_carrying_a_feed_fails_in_the_direction_that_carries_forward():
    """``ok=False`` is what makes issue 0014 republish last night's events.

    Trusting the body would build a calendar on a URL the server says is not
    there; failing means the space keeps yesterday's events until a human looks.
    """
    result = parse_rss(
        fetched(load("ace-calendar-feed-404.rss"), status_code=404, url=ACE_TRAP_URL),
        pubdate_means="event_start",
        mode="events",
        today=TODAY,
        now=NOW,
    )

    assert isinstance(result, IcsParse)  # the shape carry-forward reads
    assert result.ok is False
    assert result.problem.value == "http_error"
    assert result.problem.value not in ("not_modified", "none")


# --------------------------------------------------------------------------- trap 2


def test_a_404_with_an_rss_content_type_over_html_is_rejected():
    """**Trap 2.** Here the *header* lies, where trap 1's body did.

    The Crucible's bogus-category feed answers 404 with
    ``Content-Type: application/rss+xml`` over 1.35 MB of HTML error page.
    """
    result = parse_rss(
        fetched(
            load("crucible-bogus-category.html"),
            content_type="application/rss+xml",
            status_code=404,
            space_id="the-crucible",
            label="public-events-seed",
            url="https://www.thecrucible.org/category/this-does-not-exist-zzz/feed/",
        ),
        pubdate_means="post_date",
        mode="seed_list",
        today=TODAY,
        now=NOW,
    )

    assert result.ok is False
    assert result.events == ()
    assert result.seed_urls == ()
    assert result.body_shape == "html"
    assert result.declared_content_type == "application/rss+xml"
    # Both facts were seen, and both are in the record.
    assert any("not a feed" in note for note in result.disagreements)
    assert any("HTTP 404" in note for note in result.disagreements)


def test_the_rss_content_type_does_not_win_at_200_either():
    """**The header must not win.**

    Remove the 404 and the body is still HTML, so it is still refused — this time
    as ``NOT_FEED``, decided by the bytes alone. A parser that trusted the
    declared type would hand this to an XML reader, get zero items, and report
    "the feed is empty".
    """
    result = parse_rss(
        fetched(
            load("crucible-bogus-category.html"),
            content_type="application/rss+xml",
            status_code=200,
        ),
        pubdate_means="post_date",
        mode="seed_list",
        today=TODAY,
        now=NOW,
    )

    assert result.ok is False
    assert result.problem is RssProblem.NOT_FEED
    assert result.body_shape == "html"
    assert result.item_count == 0
    assert "header lies" in (result.error or "")
    assert result.declared_content_type == "application/rss+xml"


def test_the_body_sniffer_reads_bytes_and_not_headers():
    assert body_shape(load("crucible-bogus-category.html")) == "html"
    assert body_shape(load("sudoroom-events.rss")) == "rss"
    assert body_shape(load("sudoroom-wiki.atom")) == "atom"
    assert looks_like_feed(load("sudoroom-events.rss")) is True
    assert looks_like_feed(load("crucible-bogus-category.html")) is False
    assert looks_like_html(load("crucible-bogus-category.html")) is True
    # An XML declaration and a leading comment are skipped: the first *element*
    # decides, which is how both fixtures above are shaped.
    assert body_shape('<?xml version="1.0"?><!-- note --><rss><channel/></rss>') == "rss"
    assert parse_feed(load("crucible-bogus-category.html")) is None


# ------------------------------------------------------- the third fact: content type


def test_a_real_feed_under_a_wrong_content_type_is_a_reported_disagreement():
    """Content type and parse result disagree, so neither is picked."""
    result = parse_rss(
        fetched(load("sudoroom-mastodon.rss"), content_type="text/html"),
        pubdate_means="post_date",
        today=TODAY,
        now=NOW,
    )

    assert result.ok is False
    assert result.problem is RssProblem.WRONG_CONTENT_TYPE
    assert result.flavor == "rss"  # the body really was a feed
    assert "disagree" in (result.error or "")


def test_text_plain_over_a_real_feed_is_tolerated_and_recorded():
    """Sloppy, not dishonest — and never silent."""
    assert "text/plain" in LENIENT_CONTENT_TYPES

    result = parse_rss(
        fetched(load("sudoroom-mastodon.rss"), content_type="text/plain"),
        pubdate_means="post_date",
        today=TODAY,
        now=NOW,
    )

    assert result.ok is True
    assert result.item_count == 4
    assert any("tolerated" in note for note in result.disagreements)


def test_strict_content_type_can_be_turned_off_deliberately():
    result = parse_rss(
        fetched(load("sudoroom-mastodon.rss"), content_type="text/html"),
        pubdate_means="post_date",
        strict_content_type=False,
        today=TODAY,
        now=NOW,
    )

    assert result.ok is True
    assert result.item_count == 4


# --------------------------------------------------------------------------- transport


def test_a_304_is_reported_as_not_modified_and_never_as_zero():
    result = parse_rss(
        fetched(b"", status_code=304, outcome=Outcome.NOT_MODIFIED),
        pubdate_means="event_start",
        mode="events",
        today=TODAY,
        now=NOW,
    )

    assert result.ok is False
    assert result.problem is RssProblem.NOT_MODIFIED
    assert "reuse the stored events" in (result.error or "")


def test_a_transport_failure_is_reported_not_raised():
    result = parse_rss(
        fetched(b"", status_code=None, outcome=Outcome.FAILED, reason="timed out"),
        pubdate_means="post_date",
        today=TODAY,
        now=NOW,
    )

    assert result.ok is False
    assert result.problem is RssProblem.TRANSPORT
    assert "timed out" in (result.error or "")


def test_an_empty_body_is_its_own_problem():
    result = parse_rss(
        fetched(b"", status_code=200),
        pubdate_means="post_date",
        today=TODAY,
        now=NOW,
    )

    assert result.ok is False
    assert result.problem is RssProblem.EMPTY_BODY


def test_the_adapter_never_raises_on_garbage():
    for payload in ("", "not xml at all", "<rss", "\x00\x01\x02", "{}"):
        result = parse_rss_text(payload, pubdate_means="post_date", today=TODAY, now=NOW)
        assert result.ok is False
        assert result.problem in (RssProblem.NOT_FEED, RssProblem.NO_ITEMS)


# --------------------------------------------------------------------------- registry


def test_the_registry_requires_a_pubdate_declaration_on_every_rss_source():
    with pytest.raises(ValueError, match="requires pubdate_means"):
        Source(adapter="rss", url="https://example.test/feed/", label="x")


def test_the_registry_rejects_an_unknown_declaration():
    with pytest.raises(ValueError, match="unknown pubdate_means"):
        Source(
            adapter="rss",
            url="https://example.test/feed/",
            pubdate_means="probably_the_event",
        )
    with pytest.raises(ValueError, match="unknown rss_mode"):
        Source(
            adapter="rss",
            url="https://example.test/feed/",
            pubdate_means="post_date",
            rss_mode="publish_everything",
        )


def test_the_registry_rejects_the_one_pairing_that_would_lie():
    """``rss_mode: events`` over a feed whose pubDate is the post date."""
    with pytest.raises(ValueError, match="requires pubdate_means"):
        Source(
            adapter="rss",
            url="https://example.test/feed/",
            pubdate_means="post_date",
            rss_mode="events",
        )


def test_the_declaration_belongs_to_the_rss_adapter_alone():
    with pytest.raises(ValueError, match="takes no pubdate_means"):
        Source(
            adapter="ics",
            url="https://example.test/feed.ics",
            pubdate_means="post_date",
        )


def test_the_default_mode_is_change_detection_and_publishes_nothing():
    source = Source(
        adapter="rss", url="https://example.test/feed/", pubdate_means="post_date"
    )

    assert source.rss_mode is None
    assert source.effective_rss_mode == RssMode.CHANGE_DETECTION.value
    assert (
        Source(adapter="ics", url="https://example.test/f.ics").effective_rss_mode is None
    )


def test_the_loader_and_the_adapter_share_one_vocabulary():
    assert RSS_MODES == {"events", "change_detection", "seed_list"}
    assert PUBDATE_MEANINGS == {"event_start", "post_date"}
    assert {mode.value for mode in RssMode} == RSS_MODES
    assert {value.value for value in PubDate} == PUBDATE_MEANINGS


def test_the_three_registered_consumers_are_declared_as_the_survey_found_them(
    registry: Registry,
):
    """The 2026-08-05 table, encoded in the registry rather than in a comment."""
    declared = {
        f"{ref.space.id}:{ref.source.label}": (
            ref.source.pubdate_means,
            ref.source.effective_rss_mode,
            ref.source.trust,
            ref.source.enabled,
        )
        for ref in registry.all_sources
        if ref.source.adapter == "rss"
    }

    assert declared == {
        # The one genuine case in the registry.
        "sudo-room:events-rss": ("event_start", "events", 40, True),
        # Announcement signal only.
        "sudo-room:mastodon": ("post_date", "change_detection", 10, True),
        # Seed list only; dates live in the title text.
        "the-crucible:public-events-seed": ("post_date", "seed_list", 20, False),
    }


def test_exactly_one_registered_feed_claims_its_pubdate_is_an_event_start(
    registry: Registry,
):
    claiming = [
        f"{ref.space.id}:{ref.source.label}"
        for ref in registry.all_sources
        if ref.source.adapter == "rss" and ref.source.pubdate_means == "event_start"
    ]

    assert claiming == ["sudo-room:events-rss"]


def test_a_registry_source_missing_the_declaration_fails_to_load(tmp_path: Path):
    path = tmp_path / "sources.yaml"
    path.write_text(
        "defaults:\n"
        "  timezone: America/Los_Angeles\n"
        "  user_agent: test/0.1 (+https://maker-calendar.test/about)\n"
        "spaces:\n"
        "  - id: s\n"
        "    name: S\n"
        "    city: Oakland\n"
        "    region: east-bay\n"
        "    url: https://example.test/\n"
        "    sources:\n"
        "      - adapter: rss\n"
        "        url: https://example.test/feed/\n"
        "        label: feed\n",
        encoding="utf-8",
    )

    with pytest.raises(RegistryError, match="requires pubdate_means"):
        load_registry(path, env={"MAKER_CALENDAR_CONTACT": TEST_CONTACT})


# --------------------------------------------------------------------------- fetching


def test_the_registered_declaration_reaches_the_adapter_through_the_ref(
    registry: Registry, events_ref: SourceRef, tmp_path: Path
):
    """No argument is passed: everything comes from ``sources.yaml``."""
    router = Router({SUDO_EVENTS_URL: rss_response(load("sudoroom-events.rss"))})
    fetcher = make_fetcher(registry, router, tmp_path)

    result = fetch_rss(fetcher, events_ref, today=TODAY, now=NOW, horizon_days=120)
    fetcher.close()

    assert result.ok
    assert result.declared is True
    assert result.mode is RssMode.EVENTS
    assert result.pubdate_means is PubDate.EVENT_START
    assert result.event_count == 4
    assert router.urls == [SUDO_EVENTS_URL]


def test_the_mastodon_source_fetches_and_publishes_nothing(
    registry: Registry, mastodon_ref: SourceRef, tmp_path: Path
):
    router = Router({"sfba.social": rss_response(load("sudoroom-mastodon.rss"))})
    fetcher = make_fetcher(registry, router, tmp_path)

    result = fetch_rss(fetcher, mastodon_ref, today=TODAY, now=NOW, horizon_days=120)
    fetcher.close()

    assert result.ok
    assert result.mode is RssMode.CHANGE_DETECTION
    assert result.event_count == 0
    assert result.reported_change_signal is True
    assert result.item_count == 4


def test_the_adapter_makes_exactly_one_request_and_follows_nothing(
    registry: Registry, seed_ref: SourceRef, tmp_path: Path
):
    """A seed list is *reported*. Following it is the consuming adapter's job."""
    router = Router(
        {
            "category/upcoming-events/feed": rss_response(
                load("crucible-upcoming-events.rss")
            )
        }
    )
    fetcher = make_fetcher(registry, router, tmp_path)

    result = fetch_rss(fetcher, seed_ref, today=TODAY, now=NOW, horizon_days=120)
    fetcher.close()

    assert result.ok
    assert result.seed_count == 2
    assert len(router.urls) == 1
    assert all("/event/" not in url for url in router.urls)


def test_a_404_over_a_feed_body_reaches_the_adapter_intact(
    registry: Registry, events_ref: SourceRef, tmp_path: Path
):
    """The fetch layer keeps the status *and* the body; the adapter decides."""
    router = Router(
        {SUDO_EVENTS_URL: rss_response(load("ace-calendar-feed-404.rss"), 404)}
    )
    fetcher = make_fetcher(registry, router, tmp_path)

    result = fetch_rss(fetcher, events_ref, today=TODAY, now=NOW, horizon_days=120)
    fetcher.close()

    assert result.http_status == 404
    assert result.problem is RssProblem.HTTP_ERROR
    assert result.item_count == 3  # the body was read, and is in the report
    assert result.event_count == 0


# --------------------------------------------------------------------------- staleness


def test_a_post_date_feed_reports_its_age_from_its_newest_post():
    result = parse_rss_text(
        load("sudoroom-mastodon.rss"), pubdate_means="post_date", today=TODAY, now=NOW
    )

    assert result.last_change == dt.datetime(
        2026, 8, 2, 21, 14, 3, tzinfo=dt.timezone.utc
    )
    assert result.stale_days is not None
    assert 2 < result.stale_days < 3


def test_an_event_start_feed_is_never_dated_by_its_own_future_items():
    """Sudo Room's newest item is 2027-01-01. Calling that "fresh" would be the
    same error as publishing an announcement date as a start."""
    result = sudo_events()

    # The channel's own lastBuildDate, and nothing from the items.
    assert result.last_change == dt.datetime(2026, 8, 4, 23, 12, 44, tzinfo=dt.timezone.utc)
    assert result.dtstamp is None
    assert result.stale_days is not None and result.stale_days > 0
    assert result.liveness is not None
    assert result.liveness.dates_are_post_dates is False
    assert result.liveness.age_days is not None  # from lastBuildDate, not the items


# --------------------------------------------------------------------------- shape


def test_the_parse_is_the_shape_every_consumer_already_reads():
    result = sudo_events()

    assert isinstance(result, IcsParse)
    assert isinstance(result, RssParse)
    assert all(isinstance(event, IcsEvent) for event in result.events)
    assert all(isinstance(event, RssEvent) for event in result.events)
    assert len(result) == result.event_count == 4
    assert bool(result) is True
    assert [event.title for event in result] == titles(result)


def test_the_signal_serializes_for_health_json():
    result = parse_rss_text(
        load("sudoroom-mastodon.rss"),
        pubdate_means="post_date",
        mode="change_detection",
        today=TODAY,
        now=NOW,
    )
    payload = result.as_signal()

    assert payload["mode"] == "change_detection"
    assert payload["pubdate_means"] == "post_date"
    assert payload["declared"] is True
    assert payload["emits_events"] is False
    assert payload["change_signal"] is True
    assert payload["item_count"] == 4
    assert payload["event_count"] == 0
    assert payload["liveness"]["item_count"] == 4
    assert payload["body_digest"] == result.body_digest
