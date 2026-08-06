"""RSS output for the "what was just announced" feed.

Builds ``out/feed.xml`` with ``feedgen`` from the same deduped event set
:mod:`pipeline.emit_ics` publishes, so the two never disagree. Everything that
is shared with the ICS emit is *imported* from it rather than restated —
:func:`~pipeline.emit_ics.truncate`, :func:`~pipeline.emit_ics.event_link`,
:func:`~pipeline.emit_ics.build_summary`,
:func:`~pipeline.emit_ics.publishable`, :func:`~pipeline.emit_ics.space_index`
and :func:`~pipeline.emit_ics.write_atomic`. Two truncation policies that drift
apart would mean a subscriber reading the same event twice at two different
lengths, and the ~300-character cut is a copyright position, not a formatting
preference.

``pubDate`` is when *we* first saw it
-------------------------------------

The entire point of this file. A reader sorts by ``pubDate`` and shows the top
of the list, so ``pubDate`` answers "what was announced" — not "what is next".
The value comes from :meth:`pipeline.store.Store.first_seen`, which issue 0013
writes exactly once per ``uid`` and never updates. That guarantee is what makes
the feed safe: a space fixing a typo changes ``content_hash`` and nothing else,
so the item does not move to the top and nobody is re-notified about an event
they already saw. Using ``start_utc`` here, or re-dating on a content change,
would turn every corrected title into a push notification for every subscriber.

Ordering and the cap follow from the same decision. Items are sorted **newest
first seen** and the feed keeps at most :data:`MAX_ITEMS` of them, cut by
recency of ``first_seen`` rather than by event date — an event announced
tonight for next March belongs at the top, and an event announced last year for
tomorrow does not. Capping by event date instead would quietly evict exactly the
items the feed exists to carry.

Where ``pubDate`` is read from
------------------------------

:func:`first_seen_for` prefers a live store lookup, falls back to
:attr:`Event.first_seen <pipeline.normalize.Event.first_seen>` (which
:meth:`~pipeline.store.Store.record_events` already reconciles to the stored
value on the way through the run loop), and finally to the feed's build time.
The fallback chain matters for ``--dry-run``: a dry run records nothing, so its
events carry the run timestamp and every item is "announced tonight" — correct
for a staged artifact nobody subscribes to.

Categories
----------

One ``<category>`` per tag on :attr:`Event.categories
<pipeline.normalize.Event.categories>`. Today those are the *source's* own
categories; issue 0029 replaces them with the LLM-assigned taxonomy and this
code does not change when it does. An event with no categories emits **no**
``<category>`` element rather than an empty one, and :func:`validate_rss`
asserts that — an empty ``<category></category>`` is the kind of thing a reader
renders as a blank tag chip forever.

Validate, then move into place
------------------------------

Same contract as the ICS emit: render, round-trip the bytes back through an XML
parser, assert the feed is publishable, and only then
:func:`~pipeline.emit_ics.write_atomic` it over the live file. A half-written
``feed.xml`` served to subscribers is worse than a stale one.

Implemented by issue 0018.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Protocol
from xml.etree import ElementTree as ET

from feedgen.feed import FeedGenerator

from pipeline.config import OUT_DIR, Space
from pipeline.emit_ics import (
    CALNAME,
    DESCRIPTION_LIMIT,
    ELLIPSIS,
    EmitError,
    SpaceLookup,
    UnknownSpaceError,
    build_summary,
    event_link,
    publishable,
    space_index,
    truncate,
    write_atomic,
)
from pipeline.normalize import Event

LOG = logging.getLogger("pipeline.emit_rss")

# --------------------------------------------------------------------------- knobs

#: The feed, relative to ``out/``.
FEED_NAME = "feed.xml"

#: What the feed calls itself. Same name as the calendar, deliberately — they
#: are two views of one dataset and a reader showing a different name would
#: imply otherwise.
FEED_TITLE = CALNAME

FEED_DESCRIPTION = (
    "Newly announced events at Bay Area makerspaces. Items appear when we first "
    "see them, not in event order, so the top of the feed is what was just "
    "announced."
)

#: The public page the feed is "about". The website is a separate project (see
#: CLAUDE.md); this is the handoff URL and a caller may override it.
FEED_LINK = "https://brennan.sstools.co/maker-calendar/"

FEED_LANGUAGE = "en"

#: Ceiling on items. A feed that grows without bound eventually costs every
#: subscriber a megabyte a night to learn nothing. Cut by recency of
#: ``first_seen`` — see the module docstring.
MAX_ITEMS = 200

#: Marks a description that ran past :data:`DESCRIPTION_LIMIT`. Imported from
#: the ICS emit so the two cuts look identical.
TRUNCATION_MARKER = ELLIPSIS

UTC = dt.timezone.utc


# --------------------------------------------------------------------------- errors


class InvalidFeedError(EmitError):
    """The rendered feed failed round-trip validation.

    Raised *before* anything is moved into place, so the previously published
    ``feed.xml`` survives untouched. Same reasoning as
    :class:`~pipeline.emit_ics.InvalidCalendarError`.
    """


# --------------------------------------------------------------------------- store seam


class FirstSeenSource(Protocol):
    """Anything that can answer "when did we first see this uid?".

    Structural rather than an import of :class:`~pipeline.store.Store`, so this
    module stays underneath the store and ``--dry-run``'s
    :class:`~pipeline.store.ReadOnlyStore` façade satisfies it for free.
    """

    def first_seen(self, uid: str) -> dt.datetime | None: ...  # pragma: no cover


# --------------------------------------------------------------------------- results


@dataclass(frozen=True)
class FeedValidation:
    """What a round-trip through an XML parser found. Reported, not just asserted."""

    label: str
    item_count: int
    guids: tuple[str, ...] = ()
    pub_dates: tuple[dt.datetime, ...] = ()
    category_count: int = 0

    @property
    def unique_guids(self) -> int:
        return len(set(self.guids))

    @property
    def newest(self) -> dt.datetime | None:
        return self.pub_dates[0] if self.pub_dates else None

    @property
    def oldest(self) -> dt.datetime | None:
        return self.pub_dates[-1] if self.pub_dates else None

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        return f"{self.label}: {self.item_count} items, {self.unique_guids} guids"


@dataclass(frozen=True)
class RssEmitResult:
    """The feed written, and the accounting that justifies it.

    Deliberately the same shape as :class:`~pipeline.emit_ics.EmitResult`:
    a path, counts, a timestamp, the validations, and a
    :meth:`summary` that ``health.json`` can take verbatim.
    """

    feed_path: Path
    item_count: int = 0
    #: Publishable events considered, before the :data:`MAX_ITEMS` cap.
    candidate_count: int = 0
    counts_by_space: dict[str, int] = field(default_factory=dict)
    #: Quarantined events refused at the door, by :func:`publishable`.
    skipped_quarantined: int = 0
    #: Events that fell off the end of the cap. Not an error — see the cap note.
    dropped_over_cap: int = 0
    generated_at: dt.datetime | None = None
    newest_first_seen: dt.datetime | None = None
    oldest_first_seen: dt.datetime | None = None
    validations: tuple[FeedValidation, ...] = ()

    @property
    def paths(self) -> tuple[Path, ...]:
        return (self.feed_path,)

    @property
    def space_count(self) -> int:
        return len(self.counts_by_space)

    def summary(self) -> dict[str, Any]:
        """JSON-ready counts, for ``health.json`` (issue 0017)."""
        return {
            "feed": str(self.feed_path),
            "items": self.item_count,
            "candidates": self.candidate_count,
            "spaces": self.space_count,
            "by_space": dict(self.counts_by_space),
            "skipped_quarantined": self.skipped_quarantined,
            "dropped_over_cap": self.dropped_over_cap,
            "generated_at": (
                self.generated_at.isoformat() if self.generated_at else None
            ),
            "newest_first_seen": (
                self.newest_first_seen.isoformat() if self.newest_first_seen else None
            ),
            "oldest_first_seen": (
                self.oldest_first_seen.isoformat() if self.oldest_first_seen else None
            ),
        }

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        return f"{self.feed_path.name}: {self.item_count} items"


# --------------------------------------------------------------------------- pubDate


def first_seen_for(
    event: Event,
    store: FirstSeenSource | None = None,
    *,
    default: dt.datetime | None = None,
) -> dt.datetime:
    """When we first saw *event* — the value that becomes ``pubDate``.

    The store wins when it has a row, because that column is the one issue 0013
    promises never to rewrite. :attr:`Event.first_seen` is the fallback and is
    normally the same number: the run loop hands us the events
    :meth:`~pipeline.store.Store.record_events` reconciled. *default* (the feed
    build time) is the last resort, for an event that has never been recorded —
    a dry run, or a carry-forward set assembled outside the store.

    Never returns a naive datetime; a naive ``pubDate`` would be rendered in
    whatever zone ``feedgen`` guessed and silently reorder the feed.
    """
    stamp: dt.datetime | None = None
    if store is not None:
        stamp = store.first_seen(event.uid)
    if stamp is None:
        stamp = event.first_seen
    if stamp is None:
        stamp = default if default is not None else dt.datetime.now(UTC)
    if stamp.tzinfo is None or stamp.tzinfo.utcoffset(stamp) is None:
        raise InvalidFeedError(
            f"{event.uid}: first_seen is naive ({stamp.isoformat()}). pubDate is "
            "an instant; guessing a zone here would reorder the feed."
        )
    return stamp.astimezone(UTC)


def sort_by_first_seen(
    events: Iterable[Event],
    store: FirstSeenSource | None = None,
    *,
    default: dt.datetime | None = None,
) -> list[tuple[Event, dt.datetime]]:
    """``(event, first_seen)`` newest first, ties broken by UID.

    Newest first is what a reader shows at the top, and the UID tiebreak keeps
    the output byte-identical for an unchanged event set — the same
    determinism :func:`~pipeline.emit_ics.sort_events` defends, so a diff of two
    nights' feeds means something.
    """
    dated = [
        (event, first_seen_for(event, store, default=default)) for event in events
    ]
    dated.sort(key=lambda pair: (-pair[1].timestamp(), pair[0].uid))
    return dated


def cap(
    dated: Sequence[tuple[Event, dt.datetime]], limit: int = MAX_ITEMS
) -> tuple[list[tuple[Event, dt.datetime]], int]:
    """Keep the *limit* most recently **seen** items. Returns ``(kept, dropped)``.

    ``dated`` is expected sorted by :func:`sort_by_first_seen`, so this is a
    slice rather than a second sort. Capping by event date instead would evict
    exactly the far-future announcements this feed exists to carry.
    """
    if limit is None or limit <= 0 or len(dated) <= limit:
        return list(dated), 0
    return list(dated[:limit]), len(dated) - limit


# --------------------------------------------------------------------------- text


def _day_text(day: dt.date) -> str:
    """``Tuesday, September 1, 2026``. Built by hand because ``%-d`` is not portable."""
    return f"{day.strftime('%A')}, {day.strftime('%B')} {day.day}, {day.year}"


def _clock_text(when: dt.datetime) -> str:
    """``7:00 PM``. ``%I`` would zero-pad, which reads like a timestamp."""
    hour = when.hour % 12 or 12
    suffix = "AM" if when.hour < 12 else "PM"
    return f"{hour}:{when.minute:02d} {suffix}"


def format_event_date(event: Event) -> str:
    """The event's date, in the space's own wall clock.

    All-day events are rendered from the local **inclusive** dates issue 0009
    preserved, for the reason ``emit_ics`` gives: reading an all-day event off
    ``start_utc`` puts it on the previous day for anyone east of here.
    """
    if event.all_day:
        start = event.start_date or event.start_local.date()
        end = event.end_date or start
        if end > start:
            return f"{_day_text(start)} – {_day_text(end)} (all day)"
        return f"{_day_text(start)} (all day)"

    local = event.start_local
    text = f"{_day_text(local.date())}, {_clock_text(local)}"
    zone = local.strftime("%Z")
    return f"{text} {zone}" if zone else text


def build_item_description(
    event: Event,
    space: Space,
    *,
    limit: int = DESCRIPTION_LIMIT,
    prefer_event_url: bool = True,
) -> str:
    """Summary text, then the event date, the price and the link.

    The body prefers the model's one-line summary when ``enrich`` has written
    one (issue 0029) and otherwise truncates the source description through
    :func:`~pipeline.emit_ics.truncate` — the *same* function the ICS
    description uses, so the two cuts can never drift apart.

    The date/space/link tail is always present, including when the source
    supplied no description at all: "link back to the source page on every
    event" is the promise, and a feed item with no date in it is unreadable.
    """
    link = event_link(event, space, prefer_event_url=prefer_event_url)
    body = truncate(event.summary_line or event.description, limit)

    tail: list[str] = [format_event_date(event)]
    if event.price:
        tail.append(f"Price: {event.price}")
    tail.append(f"{space.name} — {link}" if link else space.name)
    joined = "\n".join(tail)
    return f"{body}\n\n{joined}" if body else joined


def item_categories(event: Event) -> tuple[str, ...]:
    """Non-empty tags, in order, de-duplicated.

    Source categories today, LLM tags after issue 0029. Blank entries are
    dropped rather than emitted: ``<category></category>`` renders as an empty
    tag chip in every reader that shows categories at all.
    """
    seen: list[str] = []
    for raw in event.categories or ():
        tag = (raw or "").strip()
        if tag and tag not in seen:
            seen.append(tag)
    return tuple(seen)


# --------------------------------------------------------------------------- build


def build_feed(
    events: Iterable[Event],
    spaces: SpaceLookup,
    *,
    store: FirstSeenSource | None = None,
    generated_at: dt.datetime | None = None,
    title: str = FEED_TITLE,
    description: str = FEED_DESCRIPTION,
    link: str = FEED_LINK,
    self_link: str | None = None,
    language: str = FEED_LANGUAGE,
    limit: int = MAX_ITEMS,
    prefer_event_url: bool = True,
    description_limit: int = DESCRIPTION_LIMIT,
) -> tuple[FeedGenerator, list[tuple[Event, dt.datetime]]]:
    """Assemble the feed. Returns ``(generator, the items it holds)``.

    The items come back alongside the generator because every count in
    :class:`RssEmitResult` is about them, and re-deriving the order from the
    rendered XML would be trusting the thing under test.
    """
    index = space_index(spaces)
    generated_at = (generated_at or dt.datetime.now(UTC)).astimezone(UTC)

    dated = sort_by_first_seen(events, store, default=generated_at)
    kept, _ = cap(dated, limit)

    feed = FeedGenerator()
    feed.title(title)
    feed.description(description)
    # Order matters: feedgen's channel <link> is the *last* link registered,
    # despite the comment in its source saying "the first". Self first, so the
    # human-facing alternate wins the RSS <link> element.
    if self_link:
        feed.link(href=self_link, rel="self")
    feed.link(href=link, rel="alternate")
    feed.language(language)
    feed.lastBuildDate(generated_at)
    feed.docs("http://www.rssboard.org/rss-specification")

    for event, seen_at in kept:
        space = index.get(event.space_id)
        if space is None:
            raise UnknownSpaceError(
                f"event {event.uid!r} names space {event.space_id!r}, which is "
                "not in the registry. The item's title prefix, link and "
                "attribution all come from the space; an unattributed item is "
                "the one thing we have promised not to publish."
            )

        # append, not feedgen's prepend default — the sort above is the order.
        entry = feed.add_entry(order="append")
        entry.title(build_summary(event, space))
        entry.link(href=event_link(event, space, prefer_event_url=prefer_event_url))
        # Not a permalink: the UID is our stability contract, not a URL.
        entry.guid(event.uid, permalink=False)
        entry.description(
            build_item_description(
                event,
                space,
                limit=description_limit,
                prefer_event_url=prefer_event_url,
            )
        )
        # The whole point. Not event.start_utc.
        entry.pubDate(seen_at)
        for tag in item_categories(event):
            entry.category(term=tag)

    return feed, kept


def render(feed: FeedGenerator) -> bytes:
    """Serialize to RSS 2.0 bytes."""
    return feed.rss_str(pretty=True)


# --------------------------------------------------------------------------- validate


def validate_rss(
    data: bytes | str,
    *,
    expected_count: int,
    tolerance: int = 0,
    label: str = "feed",
) -> FeedValidation:
    """Round-trip *data* through an XML parser and assert it is publishable.

    Asserts: it parses; the root is ``rss`` version 2.0 with one ``channel``
    carrying ``title``/``link``/``description``; the item count is within
    *tolerance* of what we meant to emit; every item has a non-empty title,
    link, description and ``guid``; guids are unique; every ``pubDate`` parses
    as RFC 822 and is timezone-aware; items are ordered newest ``pubDate``
    first; and no ``<category>`` is empty.

    Raises :class:`InvalidFeedError` on any of those. Never writes, never
    repairs — a caller that catches this must leave the live file alone.
    """
    payload = data.encode("utf-8") if isinstance(data, str) else data

    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise InvalidFeedError(
            f"{label}: does not parse as XML ({exc}). Nothing was published; the "
            "previous feed is still in place."
        ) from exc

    if root.tag != "rss":
        raise InvalidFeedError(f"{label}: root element is {root.tag!r}, not <rss>")
    if root.get("version") != "2.0":
        raise InvalidFeedError(
            f"{label}: rss version is {root.get('version')!r}, not '2.0'"
        )

    channels = root.findall("channel")
    if len(channels) != 1:
        raise InvalidFeedError(f"{label}: found {len(channels)} <channel>, expected 1")
    channel = channels[0]
    for required in ("title", "link", "description"):
        node = channel.find(required)
        if node is None or not (node.text or "").strip():
            raise InvalidFeedError(f"{label}: channel has no {required}")

    items = channel.findall("item")
    drift = abs(len(items) - expected_count)
    if drift > tolerance:
        raise InvalidFeedError(
            f"{label}: round-trip found {len(items)} items, expected "
            f"{expected_count} (tolerance {tolerance}). A count that moves "
            "between building and re-reading means the serializer and the "
            "parser disagree, and neither is worth trusting until that is "
            "understood."
        )

    guids: list[str] = []
    pub_dates: list[dt.datetime] = []
    categories = 0

    for position, item in enumerate(items):
        where = f"{label}: item {position}"
        for required in ("title", "link", "description"):
            node = item.find(required)
            if node is None or not (node.text or "").strip():
                raise InvalidFeedError(f"{where} has no {required}")

        guid_node = item.find("guid")
        guid = (guid_node.text or "").strip() if guid_node is not None else ""
        if not guid:
            raise InvalidFeedError(
                f"{where} has no guid. Readers de-duplicate on it; without one "
                "every item is new on every poll."
            )
        guids.append(guid)

        pub_node = item.find("pubDate")
        if pub_node is None or not (pub_node.text or "").strip():
            raise InvalidFeedError(
                f"{where} ({guid}) has no pubDate. It is the sort key and the "
                "reason this feed exists."
            )
        try:
            when = parsedate_to_datetime((pub_node.text or "").strip())
        except (TypeError, ValueError) as exc:
            raise InvalidFeedError(
                f"{where} ({guid}) has an unparseable pubDate "
                f"{pub_node.text!r} ({exc})"
            ) from exc
        if when.tzinfo is None or when.tzinfo.utcoffset(when) is None:
            raise InvalidFeedError(
                f"{where} ({guid}) has a naive pubDate {pub_node.text!r}"
            )
        pub_dates.append(when.astimezone(UTC))

        for category in item.findall("category"):
            if not (category.text or "").strip():
                raise InvalidFeedError(
                    f"{where} ({guid}) carries an empty <category>. An event "
                    "with no tags emits none at all."
                )
            categories += 1

    duplicates = sorted({guid for guid in guids if guids.count(guid) > 1})
    if duplicates:
        raise InvalidFeedError(
            f"{label}: duplicate guids {duplicates[:5]}. A reader treats one "
            "guid as one item, so a duplicate hides an announcement."
        )

    for earlier, later in zip(pub_dates, pub_dates[1:]):
        if later > earlier:
            raise InvalidFeedError(
                f"{label}: items are not newest-first by pubDate "
                f"({later.isoformat()} follows {earlier.isoformat()}). A reader "
                "shows the head of the list; the wrong head is the wrong feed."
            )

    validation = FeedValidation(
        label=label,
        item_count=len(items),
        guids=tuple(guids),
        pub_dates=tuple(pub_dates),
        category_count=categories,
    )
    LOG.debug("%s validated: %s", label, validation)
    return validation


# --------------------------------------------------------------------------- emit


def emit_rss(
    events: Iterable[Event],
    *,
    spaces: SpaceLookup,
    store: FirstSeenSource | None = None,
    out_dir: Path | str = OUT_DIR,
    generated_at: dt.datetime | None = None,
    tolerance: int = 0,
    limit: int = MAX_ITEMS,
    prefer_event_url: bool = True,
    description_limit: int = DESCRIPTION_LIMIT,
    title: str = FEED_TITLE,
    description: str = FEED_DESCRIPTION,
    link: str = FEED_LINK,
    self_link: str | None = None,
    language: str = FEED_LANGUAGE,
    feed_name: str = FEED_NAME,
) -> RssEmitResult:
    """Render, validate, then atomically publish ``out/feed.xml``.

    The entry point; issue 0012's CLI calls it beside
    :func:`~pipeline.emit_ics.emit_ics`. The feed is rendered and validated
    **before** anything is moved into place, so a failure leaves the published
    file as it was. Raises :class:`InvalidFeedError` if the round-trip
    disagrees with what was built, and
    :class:`~pipeline.emit_ics.UnknownSpaceError` if an event names a space the
    registry does not have.
    """
    out_dir = Path(out_dir)
    generated_at = (generated_at or dt.datetime.now(UTC)).astimezone(UTC)
    feed_path = out_dir / feed_name

    kept_events, skipped = publishable(events)
    feed, items = build_feed(
        kept_events,
        spaces,
        store=store,
        generated_at=generated_at,
        title=title,
        description=description,
        link=link,
        self_link=self_link,
        language=language,
        limit=limit,
        prefer_event_url=prefer_event_url,
        description_limit=description_limit,
    )
    data = render(feed)
    validation = validate_rss(
        data,
        expected_count=len(items),
        tolerance=tolerance,
        label=str(feed_path),
    )

    counts_by_space: dict[str, int] = {}
    for event, _ in items:
        counts_by_space[event.space_id] = counts_by_space.get(event.space_id, 0) + 1

    write_atomic(feed_path, data)

    result = RssEmitResult(
        feed_path=feed_path,
        item_count=len(items),
        candidate_count=len(kept_events),
        counts_by_space=counts_by_space,
        skipped_quarantined=skipped,
        dropped_over_cap=max(len(kept_events) - len(items), 0),
        generated_at=generated_at,
        newest_first_seen=items[0][1] if items else None,
        oldest_first_seen=items[-1][1] if items else None,
        validations=(validation,),
    )
    LOG.info(
        "emitted %d of %d items to %s (%d over the cap, %d quarantined withheld)",
        result.item_count,
        result.candidate_count,
        feed_path,
        result.dropped_over_cap,
        skipped,
    )
    return result


def emit_string(
    events: Sequence[Event],
    spaces: SpaceLookup,
    *,
    store: FirstSeenSource | None = None,
    generated_at: dt.datetime | None = None,
    **kwargs: Any,
) -> bytes:
    """Render a feed to bytes without touching the filesystem.

    Mirrors :func:`pipeline.emit_ics.emit_string`, for the dry-run path and for
    anything that wants the artifact without publishing it.
    """
    feed, _ = build_feed(
        events, spaces, store=store, generated_at=generated_at, **kwargs
    )
    return render(feed)


__all__ = [
    "FEED_DESCRIPTION",
    "FEED_LANGUAGE",
    "FEED_LINK",
    "FEED_NAME",
    "FEED_TITLE",
    "MAX_ITEMS",
    "TRUNCATION_MARKER",
    "FeedValidation",
    "FirstSeenSource",
    "InvalidFeedError",
    "RssEmitResult",
    "build_feed",
    "build_item_description",
    "cap",
    "emit_rss",
    "emit_string",
    "first_seen_for",
    "format_event_date",
    "item_categories",
    "render",
    "sort_by_first_seen",
    "validate_rss",
]
