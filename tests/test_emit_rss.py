"""Tests for the RSS emit (issue 0018).

**No test here touches the network**, and none of them trusts ``feedgen`` about
what it built: every assertion about the output is made after parsing the bytes
back with ``ElementTree``, because "we called ``pubDate()``" and "a reader sees
this at the top of the feed" are different claims.

What is being defended:

**``pubDate`` is when we first saw the event, not the event date.** The entire
point of the feed. Subscribers want what was just *announced*; a feed dated by
``start_utc`` is just the calendar with worse ergonomics. Asserted with the two
timestamps deliberately far apart, so "it used the right one" cannot pass by
coincidence.

**A typo fix does not re-notify anyone.** Issue 0013 writes ``first_seen`` once
per ``uid`` and never updates it. The regression this guards is a space editing
a title, ``content_hash`` moving, and every subscriber being told about an event
they already saw — tested through a real :class:`~pipeline.store.Store`, not a
stub, because the guarantee lives in that ``UPDATE``.

**Order and the cap are by recency of first seen.** An event announced tonight
for next March belongs above one announced last year for tomorrow, and the cap
must evict by announcement age rather than by event date or it drops exactly the
items the feed exists to carry.

**No empty ``<category>``.** ``Event.categories`` is routinely empty today
(``enrich`` is issue 0029); an event with no tags emits no element at all.

**Nothing half-written reaches ``out/``.** The feed is validated before anything
moves, and an interrupted write leaves the previous file with no temp files
behind it.
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import replace
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from pipeline.config import Space
from pipeline.emit_ics import DESCRIPTION_LIMIT, ELLIPSIS, UnknownSpaceError
from pipeline.emit_rss import (
    FEED_NAME,
    FEED_TITLE,
    MAX_ITEMS,
    InvalidFeedError,
    RssEmitResult,
    build_feed,
    build_item_description,
    cap,
    emit_rss,
    emit_string,
    first_seen_for,
    format_event_date,
    item_categories,
    render,
    sort_by_first_seen,
    validate_rss,
)
from pipeline.normalize import Event, QuarantineReason, content_hash
from pipeline.store import Store

#: The source-survey date, at the hour launchd runs the job.
NOW = dt.datetime(2026, 8, 5, 3, 15, tzinfo=dt.timezone.utc)
#: A month after we first saw it. Nothing may confuse the two.
START = dt.datetime(2026, 9, 1, 19, 0, tzinfo=dt.timezone.utc)
#: When we first saw it: three weeks *before* NOW.
FIRST_SEEN = dt.datetime(2026, 7, 15, 3, 15, tzinfo=dt.timezone.utc)

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
        "url": "https://acemakerspace.org/events/sewing-101/",
        "first_seen": FIRST_SEEN,
        "last_seen": NOW,
    }
    fields.update(kwargs)
    return Event(**fields)  # type: ignore[arg-type]


def hashed(**kwargs: object) -> Event:
    """An event carrying a real ``content_hash``, for the store round-trips."""
    built = event(**kwargs)
    return replace(
        built,
        content_hash=content_hash(
            title=built.title,
            start_utc=built.start_utc,
            end_utc=built.end_utc,
            location_name=built.location_name,
            address=built.address,
            url=built.url,
            description=built.description,
        ),
    )


def parse(data: bytes) -> ET.Element:
    """The channel element, from the rendered bytes. Never from the builder."""
    root = ET.fromstring(data)
    channel = root.find("channel")
    assert channel is not None
    return channel


def items(data: bytes) -> list[ET.Element]:
    return parse(data).findall("item")


def text_of(item: ET.Element, tag: str) -> str:
    node = item.find(tag)
    assert node is not None, f"item has no <{tag}>"
    return (node.text or "").strip()


def pub_date(item: ET.Element) -> dt.datetime:
    return parsedate_to_datetime(text_of(item, "pubDate")).astimezone(dt.timezone.utc)


def feed_bytes(events, spaces=SPACES, **kwargs) -> bytes:
    kwargs.setdefault("generated_at", NOW)
    return emit_string(list(events), spaces, **kwargs)


# --------------------------------------------------------------------------- shape


def test_the_feed_parses_as_rss_2_0_with_one_channel() -> None:
    root = ET.fromstring(feed_bytes([event()]))

    assert root.tag == "rss"
    assert root.get("version") == "2.0"
    assert len(root.findall("channel")) == 1


def test_channel_carries_title_link_and_description() -> None:
    channel = parse(feed_bytes([event()]))

    assert (channel.findtext("title") or "").strip() == FEED_TITLE
    assert (channel.findtext("link") or "").strip().startswith("https://")
    assert (channel.findtext("description") or "").strip()


def test_channel_link_is_the_alternate_not_the_self_link() -> None:
    """feedgen's RSS <link> is the *last* link registered, not the first.

    Its own source comment says "we use the first link for RSS" and then indexes
    ``[-1]``. Registering the self link first is what keeps the human-facing
    page in ``<link>`` — worth a test, because the bug is upstream and silent.
    """
    data = feed_bytes(
        [event()],
        link="https://example.test/calendar/",
        self_link="https://example.test/calendar/feed.xml",
    )
    channel = parse(data)

    assert (channel.findtext("link") or "").strip() == "https://example.test/calendar/"
    atom = channel.find("{http://www.w3.org/2005/Atom}link")
    assert atom is not None
    assert atom.get("href") == "https://example.test/calendar/feed.xml"


def test_item_title_carries_the_space_prefix() -> None:
    (item,) = items(feed_bytes([event()]))
    assert text_of(item, "title") == "[Ace Makerspace, Inc] Sewing 101 Bootcamp"


def test_guid_is_the_uid_verbatim_and_is_not_a_permalink() -> None:
    """The UID is the stability contract; a reader de-duplicates on it."""
    (item,) = items(feed_bytes([event()]))
    guid = item.find("guid")
    assert guid is not None
    assert (guid.text or "") == "ace-makerspace:evt-1@acemakerspace.org"
    assert guid.get("isPermaLink") == "false"


def test_link_prefers_the_event_page_and_falls_back_to_the_space() -> None:
    (with_url,) = items(feed_bytes([event()]))
    assert text_of(with_url, "link") == "https://acemakerspace.org/events/sewing-101/"

    (without,) = items(feed_bytes([event(url=None)]))
    assert text_of(without, "link") == "https://acemakerspace.org/"


def test_prefer_event_url_off_points_every_item_at_the_space_page() -> None:
    """Consistent with ``emit_ics``'s knob of the same name."""
    (item,) = items(feed_bytes([event()], prefer_event_url=False))
    assert text_of(item, "link") == "https://acemakerspace.org/"


# --------------------------------------------------------------------------- pubDate


def test_pub_date_is_first_seen_and_not_the_event_start() -> None:
    """The defining requirement. The two are deliberately weeks apart."""
    assert FIRST_SEEN != START

    (item,) = items(feed_bytes([event()]))

    assert pub_date(item) == FIRST_SEEN
    assert pub_date(item) != START


def test_a_store_row_outranks_the_value_on_the_record() -> None:
    """``store.first_seen`` is the authority; the record is the fallback."""

    class Fake:
        def first_seen(self, uid: str) -> dt.datetime:
            return dt.datetime(2026, 1, 2, 3, 4, tzinfo=dt.timezone.utc)

    (item,) = items(feed_bytes([event()], store=Fake()))
    assert pub_date(item) == dt.datetime(2026, 1, 2, 3, 4, tzinfo=dt.timezone.utc)


def test_an_event_never_recorded_falls_back_to_the_build_time() -> None:
    """The dry-run path: nothing is in the store, so everything is "tonight"."""

    class Empty:
        def first_seen(self, uid: str) -> None:
            return None

    (item,) = items(feed_bytes([event(first_seen=None)], store=Empty()))
    assert pub_date(item) == NOW


def test_first_seen_for_refuses_a_naive_timestamp() -> None:
    class Naive:
        def first_seen(self, uid: str) -> dt.datetime:
            return dt.datetime(2026, 7, 15, 3, 15)

    with pytest.raises(InvalidFeedError, match="naive"):
        first_seen_for(event(), Naive())


def test_pub_date_is_unchanged_after_the_title_is_edited() -> None:
    """The re-notification regression, through a real store.

    A space fixing a typo changes ``content_hash`` and must not change
    ``first_seen`` — otherwise the item jumps to the head of the feed and every
    subscriber is told about an event they already saw.
    """
    with Store.in_memory() as store:
        original = hashed()
        store.record_events([original], now=FIRST_SEEN)
        before = items(feed_bytes([original], store=store))[0]

        # Three weeks later the space fixes the title. Same UID, new hash.
        corrected = hashed(title="Sewing 101 Bootcamp (beginners welcome)")
        assert corrected.content_hash != original.content_hash
        merge = store.record_events([corrected], now=NOW)

        assert merge.changed_uids == (original.uid,)
        after = items(feed_bytes(list(merge.events), store=store))[0]

        assert pub_date(after) == pub_date(before) == FIRST_SEEN
        assert text_of(after, "title").endswith("(beginners welcome)"), (
            "the edit must reach the feed — only the date is pinned"
        )


# --------------------------------------------------------------------------- order


def test_a_newly_seen_event_sorts_above_an_older_one_starting_sooner() -> None:
    """Announcement order, not chronological order. This is the whole design."""
    imminent = event(
        uid="ace-makerspace:old-news",
        title="Open Shop Tomorrow",
        start_utc=NOW + dt.timedelta(days=1),
        end_utc=NOW + dt.timedelta(days=1, hours=2),
        first_seen=dt.datetime(2026, 3, 1, tzinfo=dt.timezone.utc),
    )
    far_off = event(
        uid="ace-makerspace:just-announced",
        title="Spring Intensive",
        start_utc=dt.datetime(2027, 3, 1, 19, 0, tzinfo=dt.timezone.utc),
        end_utc=dt.datetime(2027, 3, 1, 21, 0, tzinfo=dt.timezone.utc),
        first_seen=NOW,
    )

    rendered = items(feed_bytes([imminent, far_off]))
    titles = [text_of(item, "title") for item in rendered]

    assert titles[0].endswith("Spring Intensive")
    assert titles[1].endswith("Open Shop Tomorrow")
    assert pub_date(rendered[0]) > pub_date(rendered[1])


def test_ties_are_broken_by_uid_so_the_output_is_deterministic() -> None:
    same = dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)
    a = event(uid="ace-makerspace:aaa", first_seen=same)
    b = event(uid="ace-makerspace:bbb", first_seen=same)

    first = feed_bytes([b, a])
    second = feed_bytes([a, b])

    assert first == second
    guids = [text_of(item, "guid") for item in items(first)]
    assert guids == ["ace-makerspace:aaa", "ace-makerspace:bbb"]


def test_sort_by_first_seen_returns_the_timestamp_alongside_the_event() -> None:
    dated = sort_by_first_seen([event()], default=NOW)
    assert [stamp for _, stamp in dated] == [FIRST_SEEN]


# --------------------------------------------------------------------------- cap


def make_series(count: int) -> list[Event]:
    """``count`` events, seen one day apart, with event dates in reverse order.

    The event dates run *opposite* to the first-seen order on purpose: a cap
    that quietly sorted by ``start_utc`` would keep exactly the wrong half.
    """
    built: list[Event] = []
    for index in range(count):
        built.append(
            event(
                uid=f"ace-makerspace:evt-{index:03d}",
                title=f"Workshop {index:03d}",
                first_seen=FIRST_SEEN + dt.timedelta(days=index),
                start_utc=START + dt.timedelta(days=count - index),
                end_utc=START + dt.timedelta(days=count - index, hours=2),
            )
        )
    return built


def test_the_cap_keeps_the_most_recently_seen_items(tmp_path: Path) -> None:
    result = emit_rss(
        make_series(10),
        spaces=SPACES,
        out_dir=tmp_path,
        generated_at=NOW,
        limit=3,
    )

    rendered = items((tmp_path / FEED_NAME).read_bytes())
    guids = [text_of(item, "guid") for item in rendered]

    assert guids == [
        "ace-makerspace:evt-009",
        "ace-makerspace:evt-008",
        "ace-makerspace:evt-007",
    ]
    assert result.item_count == 3
    assert result.candidate_count == 10
    assert result.dropped_over_cap == 7


def test_the_default_cap_is_two_hundred() -> None:
    assert MAX_ITEMS == 200
    assert len(items(feed_bytes(make_series(205)))) == 200


def test_cap_is_a_noop_below_the_limit() -> None:
    dated = sort_by_first_seen(make_series(4), default=NOW)
    kept, dropped = cap(dated, 10)
    assert len(kept) == 4
    assert dropped == 0


# --------------------------------------------------------------------------- text


def test_description_carries_the_event_date_the_space_and_the_price() -> None:
    body = build_item_description(
        event(description="Learn to sew.", price="$45 / $30 members"), ace()
    )

    assert "Learn to sew." in body
    # 19:00 UTC on 1 September is noon Pacific.
    assert "Tuesday, September 1, 2026" in body
    assert "12:00 PM" in body
    assert "Price: $45 / $30 members" in body
    assert "Ace Makerspace, Inc" in body
    assert "https://acemakerspace.org/events/sewing-101/" in body


def test_the_date_reaches_the_rendered_item() -> None:
    (item,) = items(feed_bytes([event(description="Learn to sew.", price="$45")]))
    description = text_of(item, "description")

    assert "Tuesday, September 1, 2026" in description
    assert "Price: $45" in description
    assert "Ace Makerspace, Inc" in description


def test_a_long_description_is_truncated_and_still_ends_with_the_link() -> None:
    source = "word " * 200
    (item,) = items(feed_bytes([event(description=source)]))
    description = text_of(item, "description")

    head = description.split("\n\n")[0]
    assert len(head) <= DESCRIPTION_LIMIT + 1
    assert head.endswith(ELLIPSIS)
    assert not head.endswith("wor" + ELLIPSIS), "the cut must land on a word boundary"
    assert "https://acemakerspace.org/events/sewing-101/" in description


def test_a_short_description_is_not_truncated() -> None:
    body = build_item_description(event(description="Come make something."), ace())
    assert ELLIPSIS not in body
    assert body.startswith("Come make something.")


def test_the_enriched_summary_line_wins_over_the_source_description() -> None:
    """Issue 0029 writes ``summary_line``; the feed prefers it when it is there."""
    body = build_item_description(
        event(
            description="A very long source description that we would rather not "
            "republish wholesale.",
            summary_line="Beginner sewing class, machines provided.",
        ),
        ace(),
    )
    assert body.startswith("Beginner sewing class, machines provided.")
    assert "republish wholesale" not in body


def test_an_event_with_no_description_still_links_back() -> None:
    body = build_item_description(event(description=None), ace())
    assert "https://acemakerspace.org/events/sewing-101/" in body
    assert "September 1, 2026" in body


def test_an_all_day_event_reads_as_a_date_range() -> None:
    festival = event(
        uid="ace-makerspace:fest",
        all_day=True,
        start_date=dt.date(2026, 9, 4),
        end_date=dt.date(2026, 9, 6),
        start_utc=dt.datetime(2026, 9, 4, 7, 0, tzinfo=dt.timezone.utc),
        end_utc=dt.datetime(2026, 9, 7, 7, 0, tzinfo=dt.timezone.utc),
    )
    assert format_event_date(festival) == (
        "Friday, September 4, 2026 – Sunday, September 6, 2026 (all day)"
    )

    single = event(
        uid="ace-makerspace:one-day",
        all_day=True,
        start_date=dt.date(2026, 9, 4),
        end_date=dt.date(2026, 9, 4),
        start_utc=dt.datetime(2026, 9, 4, 7, 0, tzinfo=dt.timezone.utc),
        end_utc=dt.datetime(2026, 9, 5, 7, 0, tzinfo=dt.timezone.utc),
    )
    assert format_event_date(single) == "Friday, September 4, 2026 (all day)"


# --------------------------------------------------------------------------- categories


def test_no_categories_emits_no_category_element() -> None:
    """``Event.categories`` is empty for most sources until issue 0029."""
    assert event().categories == ()
    (item,) = items(feed_bytes([event()]))
    assert item.findall("category") == []


def test_one_category_element_per_tag() -> None:
    (item,) = items(feed_bytes([event(categories=("textiles", "class"))]))
    assert [(node.text or "") for node in item.findall("category")] == [
        "textiles",
        "class",
    ]


def test_blank_and_duplicate_tags_are_dropped_rather_than_emitted() -> None:
    assert item_categories(event(categories=("textiles", "  ", "textiles", ""))) == (
        "textiles",
    )
    (item,) = items(feed_bytes([event(categories=("", "   "))]))
    assert item.findall("category") == []


# --------------------------------------------------------------------------- refusals


def test_quarantined_events_are_never_published_and_are_counted(
    tmp_path: Path,
) -> None:
    result = emit_rss(
        [event(), event(uid="ace-makerspace:bad", quarantine=QuarantineReason.EMPTY_TITLE)],
        spaces=SPACES,
        out_dir=tmp_path,
        generated_at=NOW,
    )

    assert result.item_count == 1
    assert result.skipped_quarantined == 1
    assert len(items((tmp_path / FEED_NAME).read_bytes())) == 1


def test_an_event_naming_an_unknown_space_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(UnknownSpaceError, match="not in the registry"):
        emit_rss(
            [event(space_id="not-a-space")],
            spaces=SPACES,
            out_dir=tmp_path,
            generated_at=NOW,
        )
    assert not (tmp_path / FEED_NAME).exists()


# --------------------------------------------------------------------------- validate


def test_validation_rejects_bytes_that_are_not_xml() -> None:
    with pytest.raises(InvalidFeedError, match="does not parse"):
        validate_rss(b"<rss><channel>", expected_count=0)


def test_validation_rejects_a_non_rss_root() -> None:
    with pytest.raises(InvalidFeedError, match="not <rss>"):
        validate_rss(b"<feed><channel/></feed>", expected_count=0)


def test_validation_rejects_a_count_that_does_not_match() -> None:
    with pytest.raises(InvalidFeedError, match="expected 5"):
        validate_rss(feed_bytes([event()]), expected_count=5)


def test_validation_rejects_an_item_with_no_pub_date() -> None:
    data = feed_bytes([event()]).replace(b"<pubDate>", b"<notPubDate>").replace(
        b"</pubDate>", b"</notPubDate>"
    )
    with pytest.raises(InvalidFeedError, match="no pubDate"):
        validate_rss(data, expected_count=1)


def test_validation_rejects_an_empty_category() -> None:
    # Before the item's <guid>, so the channel description is left alone.
    data = feed_bytes([event()]).replace(b"<guid", b"<category></category><guid", 1)
    with pytest.raises(InvalidFeedError, match="empty <category>"):
        validate_rss(data, expected_count=1)


def test_validation_rejects_duplicate_guids() -> None:
    data = feed_bytes([event(), event(uid="ace-makerspace:evt-2")])
    data = data.replace(b"ace-makerspace:evt-2", b"ace-makerspace:evt-1@acemakerspace.org")
    with pytest.raises(InvalidFeedError, match="duplicate guids"):
        validate_rss(data, expected_count=2)


def test_validation_rejects_items_that_are_not_newest_first() -> None:
    older = "Wed, 01 Jul 2026 12:00:00 +0000"
    newer = "Sat, 01 Aug 2026 12:00:00 +0000"
    data = (
        "<rss version='2.0'><channel><title>t</title><link>l</link>"
        "<description>d</description>"
        f"<item><title>a</title><link>x</link><description>d</description>"
        f"<guid>1</guid><pubDate>{older}</pubDate></item>"
        f"<item><title>b</title><link>y</link><description>d</description>"
        f"<guid>2</guid><pubDate>{newer}</pubDate></item>"
        "</channel></rss>"
    )
    with pytest.raises(InvalidFeedError, match="newest-first"):
        validate_rss(data, expected_count=2)


def test_validation_reports_what_it_found() -> None:
    report = validate_rss(
        feed_bytes([event(categories=("textiles",)), event(uid="ace-makerspace:e2")]),
        expected_count=2,
        label="feed.xml",
    )
    assert isinstance(report.item_count, int)
    assert report.item_count == 2
    assert report.unique_guids == 2
    assert report.category_count == 1
    assert report.newest == FIRST_SEEN


# --------------------------------------------------------------------------- emit


def test_emit_writes_the_feed_and_reports_the_counts(tmp_path: Path) -> None:
    result = emit_rss(
        [event(), event(uid="noisebridge:e1", space_id="noisebridge", url=None)],
        spaces=SPACES,
        out_dir=tmp_path,
        generated_at=NOW,
    )

    assert isinstance(result, RssEmitResult)
    assert result.feed_path == tmp_path / FEED_NAME
    assert result.feed_path.is_file()
    assert result.item_count == 2
    assert result.counts_by_space == {"ace-makerspace": 1, "noisebridge": 1}
    assert result.space_count == 2
    assert result.generated_at == NOW
    assert result.summary()["items"] == 2
    assert result.summary()["feed"].endswith(FEED_NAME)


def test_emit_replaces_an_existing_feed_in_place(tmp_path: Path) -> None:
    target = tmp_path / FEED_NAME
    target.write_bytes(b"<rss/>")

    emit_rss([event()], spaces=SPACES, out_dir=tmp_path, generated_at=NOW)

    assert target.read_bytes().startswith(b"<?xml")
    assert [p.name for p in tmp_path.iterdir()] == [FEED_NAME]


def test_emit_of_an_empty_event_set_writes_an_empty_but_valid_feed(
    tmp_path: Path,
) -> None:
    result = emit_rss([], spaces=SPACES, out_dir=tmp_path, generated_at=NOW)

    assert result.item_count == 0
    assert items((tmp_path / FEED_NAME).read_bytes()) == []
    assert result.newest_first_seen is None


def test_a_corrupt_render_is_never_published(tmp_path: Path, monkeypatch) -> None:
    """Validation runs before anything moves: the previous file must survive."""
    target = tmp_path / FEED_NAME
    target.write_bytes(b"<rss version='2.0'><channel>PREVIOUS RUN</channel></rss>")

    monkeypatch.setattr("pipeline.emit_rss.render", lambda feed: b"garbage")

    with pytest.raises(InvalidFeedError):
        emit_rss([event()], spaces=SPACES, out_dir=tmp_path, generated_at=NOW)

    assert target.read_bytes().endswith(b"</rss>")
    assert b"PREVIOUS RUN" in target.read_bytes()
    assert list(tmp_path.glob("*.tmp")) == []


def test_a_failed_write_leaves_no_partial_file(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / FEED_NAME
    target.write_bytes(b"previous")

    def boom(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)

    with pytest.raises(OSError, match="disk full"):
        emit_rss(make_series(50), spaces=SPACES, out_dir=tmp_path, generated_at=NOW)

    assert target.read_bytes() == b"previous", "the live file must survive"
    assert list(tmp_path.iterdir()) == [target], "no temp file may be left behind"


def test_emit_string_writes_nothing(tmp_path: Path) -> None:
    data = emit_string([event()], SPACES, generated_at=NOW)
    assert data.startswith(b"<?xml")
    assert list(tmp_path.iterdir()) == []


def test_build_feed_returns_the_items_it_kept() -> None:
    feed, kept = build_feed(make_series(5), SPACES, generated_at=NOW, limit=2)
    assert len(kept) == 2
    assert len(items(render(feed))) == 2


# --------------------------------------------------------------------------- the run
#
# A run publishes both artifacts or neither. These go through ``run_pipeline``
# rather than ``emit_rss`` directly, because the thing being defended is the
# wiring: a feed that is only produced when someone remembers to call it is a
# feed that silently stops the first time the publish path is edited.

ICS_BODY = b"""BEGIN:VCALENDAR\r
VERSION:2.0\r
PRODID:-//test//maker-calendar//EN\r
BEGIN:VEVENT\r
UID:evt-1@test\r
DTSTAMP:20260804T120000Z\r
DTSTART:20260810T180000Z\r
DTEND:20260810T200000Z\r
SUMMARY:Open Shop Night\r
LOCATION:1234 Test Ave, Oakland, CA\r
DESCRIPTION:Come make something.\r
URL:https://example.test/events/1\r
END:VEVENT\r
END:VCALENDAR\r
"""


def _transport():
    import httpx

    def handler(request: "httpx.Request") -> "httpx.Response":
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200,
            content=ICS_BODY,
            headers={"Content-Type": "text/calendar; charset=utf-8"},
        )

    return httpx.MockTransport(handler)


def _run(tmp_path: Path, **kwargs):
    """``run_pipeline`` with every seam pointed somewhere harmless."""
    from pipeline.cli import LmStudioStatus, run_pipeline
    from pipeline.config import CONTACT_ENV_VAR, load_registry

    registry = load_registry(env={CONTACT_ENV_VAR: "https://maker-calendar.test/about"})
    kwargs.setdefault("transport", _transport())
    return run_pipeline(
        registry,
        out_dir=tmp_path / "out",
        raw_dir=tmp_path / "raw",
        db_path=tmp_path / "events.sqlite",
        sleep=lambda seconds: None,
        clock=lambda: 1000.0,
        now=NOW,
        llm_probe=lambda: LmStudioStatus(available=False, reason="not running"),
        **kwargs,
    )


def test_a_run_emits_both_the_calendar_and_the_feed(tmp_path: Path) -> None:
    report = _run(tmp_path)
    out_dir = tmp_path / "out"

    assert report.published is True
    assert report.emit is not None and report.rss is not None
    assert (out_dir / "calendar.ics").is_file()
    assert (out_dir / FEED_NAME).is_file()
    assert (out_dir / "calendar.ics").read_bytes().startswith(b"BEGIN:VCALENDAR")

    rendered = items((out_dir / FEED_NAME).read_bytes())
    assert len(rendered) == report.rss.item_count == report.event_count
    # The whole set was recorded tonight, so pubDate is tonight — not the
    # 10 August event start the fixture carries.
    assert all(pub_date(item) == NOW for item in rendered)
    assert all(pub_date(item) != dt.datetime(2026, 8, 10, 18, tzinfo=dt.timezone.utc)
               for item in rendered)
    assert report.as_dict()["rss"]["items"] == report.rss.item_count


def test_a_dry_run_stages_both_and_leaves_the_live_files_alone(tmp_path: Path) -> None:
    from pipeline.cli import STAGING_DIRNAME

    out_dir = tmp_path / "out"
    report = _run(tmp_path, dry_run=True)
    staging = out_dir / STAGING_DIRNAME

    assert report.published is True
    assert (staging / "calendar.ics").is_file()
    assert (staging / FEED_NAME).is_file()
    assert not (out_dir / "calendar.ics").exists()
    assert not (out_dir / FEED_NAME).exists()

    # A dry run records nothing, so first_seen falls back to the run timestamp.
    assert all(pub_date(item) == NOW for item in items((staging / FEED_NAME).read_bytes()))


def test_a_blocked_or_empty_run_publishes_neither_artifact(tmp_path: Path) -> None:
    """No events means no feed, for the same reason it means no calendar."""
    import httpx

    def handler(request: "httpx.Request") -> "httpx.Response":
        return httpx.Response(404)

    report = _run(tmp_path, transport=httpx.MockTransport(handler))

    assert report.published is False
    assert report.rss is None
    assert not (tmp_path / "out" / FEED_NAME).exists()
