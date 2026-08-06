"""RSS 2.0 and Atom. **Most RSS feeds in this registry are not event sources.**

That sentence is the whole adapter. An RSS ``<pubDate>`` is when the *post* was
published, not when the event happens, and the two are indistinguishable by
inspection: both are plausible dates in plausible places. An adapter that read
``pubDate`` as a start would publish a calendar of announcement dates — every
entry wrong, every entry believable, and nothing downstream ever saying so. That
is the worst failure mode this project has, and it is why nothing here guesses.

Per-source truth, established 2026-08-05 by fetching every one of them
---------------------------------------------------------------------

=========================================  ====================  =================
Feed                                       ``pubDate`` means     Usable as events?
=========================================  ====================  =================
Sudo Room ``/events/feed/``                **event start**       **Yes** — the one
                                                                 genuine case in
                                                                 the registry
Meetup ``/events/rss/`` (all spaces)       post date             No — every tag was
                                                                 enumerated to
                                                                 confirm there is
                                                                 no start date
                                                                 anywhere in the
                                                                 item
The Box Shop ``/events?format=rss``        post date             Seed list only
The Crucible                               post date             Seed list only;
``/category/upcoming-events/feed/``                              the dates live in
                                                                 the title text,
                                                                 as ``(JUL 16)``
Mastodon ``.rss``                          post date             Announcement
                                                                 signal only
=========================================  ====================  =================

The declaration is required, and it lives in the registry
--------------------------------------------------------

``sources.yaml`` carries **``pubdate_means``** on every ``rss`` source, and
:mod:`pipeline.config` refuses to load one without it — the same precedent
``script_id`` (issue 0021), ``shape`` and ``min_total`` (issue 0022) set:
configuration that changes what a document *means* is validated at load time and
required where it matters. ``rss_mode`` then says what the feed is *for*:

============================  ==================================================
``pubdate_means: event_start``  ``pubDate`` is the event start. Only
                                ``sudo-room`` / ``events-rss`` may say this.
``pubdate_means: post_date``    ``pubDate`` is when the post went up.
``rss_mode: events``            Publish events. Requires
                                ``pubdate_means: event_start``; the loader
                                rejects the combination that would lie.
``rss_mode: change_detection``  **The default.** Emit no events; report a
                                liveness signal for ``health.json``.
``rss_mode: seed_list``         Emit no events; report item ``<link>`` URLs for
                                another adapter to follow.
============================  ==================================================

Called without a registry entry — a test, or a human reading a file out of
``raw/`` — the mode defaults to ``change_detection`` and
:attr:`RssParse.declared` is ``False``. The safe default is the undeclared one:
an adapter that fell back to publishing events would make the missing
declaration invisible.

"Emitted no events" is three different statements
-------------------------------------------------

They must not look alike in ``health.json`` at 09:00, so they do not:

- **A change signal.** :attr:`RssParse.ok` is true, :attr:`RssParse.events` is
  empty, :attr:`RssParse.liveness` is populated, and
  :attr:`RssParse.reported_change_signal` is true. The feed was found, parsed and
  read; it is simply not a calendar. The liveness signal reaches ``health.json``
  through the fields the CLI already records: ``raw_count`` (items),
  ``last_change`` and ``stale_days``. Sequoia Fabrica's Mastodon feed is the
  case that matters — 20 items, newest 2025-12-23, roughly 7.5 months cold — and
  a count-based gate cannot tell that from a healthy feed.
- **A seed list.** Same, plus :attr:`RssParse.seed_urls`.
- **Nothing found.** :attr:`RssProblem.NO_ITEMS`, ``ok=False``. A feed that
  parsed and carried zero ``<item>`` elements is a repair, not a quiet week.

For the ``events`` mode the ordinary zero rules apply: zero *inside the horizon*
is ``ok`` and is issue 0016's ``allow_zero`` to judge, while items present and
not one carrying a usable date is :attr:`RssProblem.NO_DATES`.

Staleness is computed only from dates that are post dates
---------------------------------------------------------

:attr:`~pipeline.adapters.ics.IcsParse.last_change` feeds issue 0016's
``max_stale_days`` gate, so it has to mean "when did this feed last change". For
a ``post_date`` feed the newest item date is exactly that. For Sudo Room's
``event_start`` feed it is a date in the *future*, so item dates are deliberately
**not** used there: only a channel ``lastBuildDate`` / Atom ``<updated>`` is.
Reporting a feed as fresh because it announces an event next March is the same
class of error as publishing the announcement date as the start.

The two 404 traps, and the policy for both
------------------------------------------

Both are live, both are in ``sources.yaml``'s trap lists, and they lie in
opposite directions:

1. **Ace's ``/calendar/feed/`` answers HTTP 404 with a populated 9.8 KB valid
   RSS body.** A body-trusting parser ingests it; a status-checking parser drops
   it. Neither is right on its own, because neither one *sees* the disagreement.
2. **The Crucible's ``/category/<bogus>/feed/`` answers HTTP 404 with
   ``Content-Type: application/rss+xml`` over a 1.35 MB HTML error page.** Here
   the **header** lies, where every earlier trap in this registry had a lying
   body.

**The policy, stated once and applied to both: all three facts are collected —
status, declared content type, and parse result — and any disagreement between
them is a reported failure. Nothing is chosen because it happens to agree.**

Concretely:

- The body is parsed **first**, always, even under a 404, so the report can say
  what was actually there.
- **A non-2xx status is fatal, whatever the body turned out to be**
  (:attr:`RssProblem.HTTP_ERROR`), and the report names the contradiction: "404
  carrying a valid RSS feed with 12 items". Status wins because the server is
  telling us this resource does not exist: an endpoint that 404s is one the
  publisher does not consider published, it can change or vanish without notice,
  and in Ace's case the canonical events RSS is somewhere else entirely
  (``?post_type=tribe_events&feed=rss2``). Failing also means issue 0014 carries
  yesterday's events forward, which is the recoverable direction. Trusting the
  body would build a calendar on a URL the site says is not there.
- **A body that is not a feed is fatal even at HTTP 200 and even under an
  ``application/rss+xml`` header** (:attr:`RssProblem.NOT_FEED`). The header
  never wins: :func:`looks_like_feed` reads the bytes, and 1.35 MB of HTML is
  reported as HTML no matter what the response claimed it was.
- A declared type that is neither XML nor lenient, over a body that *is* a feed,
  is :attr:`RssProblem.WRONG_CONTENT_TYPE` — with one deliberate tolerance: some
  hosts serve a perfectly good feed as ``text/plain``. That is sloppy, not
  dishonest, and it is tolerated **only** when the body sniffs as a feed, which
  is the same shape :mod:`pipeline.adapters.ics` uses.

Every disagreement found is also recorded verbatim in
:attr:`RssParse.disagreements`, so "the header said one thing and the body
another" survives into the run report rather than being collapsed into a verdict.

What this adapter does not do
-----------------------------

It never follows a link. Seed-list mode *reports* URLs; fetching them is the
consuming adapter's job (``jsonld`` does it for The Box Shop through
:attr:`pipeline.cli.AdapterEntry.paginates`, so the follow-ups share one
``robots.txt`` decision, one rate limiter and one ``raw/`` archive). Links that
are off-host, or that carry a robots-disallowed ``?format=`` value, are dropped
from the seed list here with a reason attached — the check is
:func:`pipeline.adapters.jsonld.is_disallowed_route`, reused rather than
re-implemented so one place decides which routes are off-limits.

Implemented by issue 0025.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from enum import Enum
from typing import Any
from urllib.parse import urljoin

import httpx
import lxml.etree
from dateutil.parser import isoparse

from pipeline.adapters.ics import DEFAULT_HORIZON_DAYS, IcsEvent, IcsParse

# Shared rather than re-implemented: two adapters reading the same space must
# not disagree about what ``&amp;`` looks like, about how an offset is written,
# or about which ``?format=`` routes robots.txt puts off-limits.
from pipeline.adapters.embedded_json import DEFAULT_ZONE
from pipeline.adapters.embedded_json import (  # noqa: PLC2701
    _clean as clean,
)
from pipeline.adapters.jsonld import is_disallowed_route
from pipeline.adapters.jsonld import (  # noqa: PLC2701
    _offset_text as offset_text,
)
from pipeline.config import SourceRef
from pipeline.fetch import (
    XML_CONTENT_TYPES,
    FetchResult,
    Fetcher,
    Outcome,
    request_url,
)

# --------------------------------------------------------------------------- knobs

#: Types we tolerate **only** when the body actually sniffs as a feed. Some hosts
#: serve a good feed as ``text/plain`` or as an opaque download; that is sloppy,
#: not dishonest. ``text/html`` is deliberately not in here — the 1.35 MB HTML
#: body under an ``application/rss+xml`` header is the trap this list must not
#: widen to cover.
LENIENT_CONTENT_TYPES: tuple[str, ...] = (
    "text/plain",
    "application/octet-stream",
    "application/rdf+xml",
)

#: How far into a body to look before deciding what kind of document it is. A
#: real feed declares itself in the first line; sniffing 774 KB of Sudo Room
#: would be silly.
_SNIFF_BYTES = 4096

#: Element local-names that open one item, per flavour.
ITEM_TAGS: tuple[str, ...] = ("item", "entry")

#: Where an item's date may be written, in the order they are trusted. RSS 2.0
#: writes ``pubDate``; Atom writes ``published`` and ``updated``; RSS 1.0 / RDF
#: writes ``dc:date``. ``updated`` is last because it moves when a post is
#: edited, and an edit is not a new event.
DATE_TAGS: tuple[str, ...] = ("pubdate", "published", "date", "updated")

#: Channel-level "when did this feed change", in trust order.
FEED_DATE_TAGS: tuple[str, ...] = ("lastbuilddate", "updated", "pubdate", "date")


class RssMode(str, Enum):
    """What a registered feed is *for*. ``sources.yaml``'s ``rss_mode``."""

    #: Publish events, reading ``pubDate`` as the start. Requires
    #: ``pubdate_means: event_start``.
    EVENTS = "events"
    #: **The default.** No events; a liveness signal for ``health.json``.
    CHANGE_DETECTION = "change_detection"
    #: No events; item ``<link>`` URLs for another adapter to follow.
    SEED_LIST = "seed_list"


class PubDate(str, Enum):
    """What ``pubDate`` means in one feed. ``sources.yaml``'s ``pubdate_means``.

    There is no third value and there is no default: the registry must say, and
    :mod:`pipeline.config` refuses to load an ``rss`` source that does not.
    """

    EVENT_START = "event_start"
    POST_DATE = "post_date"


#: The vocabularies :mod:`pipeline.config` validates the registry against, the
#: way it validates ``shape`` against :data:`pipeline.adapters.json_doc.SHAPES`.
RSS_MODES: frozenset[str] = frozenset(mode.value for mode in RssMode)
PUBDATE_MEANINGS: frozenset[str] = frozenset(value.value for value in PubDate)

#: The two values the loader has to name in an error message. Exported so the
#: registry rule and the adapter cannot drift into disagreeing about the string.
EVENTS_MODE = RssMode.EVENTS.value
EVENT_START = PubDate.EVENT_START.value

#: The mode a source gets when ``sources.yaml`` names none. Change detection is
#: the only safe default: it publishes nothing.
DEFAULT_MODE = RssMode.CHANGE_DETECTION


# --------------------------------------------------------------------------- outcomes


class RssProblem(str, Enum):
    """Why an :class:`RssParse` is not usable. ``NONE`` means it is.

    The first five mirror :class:`~pipeline.adapters.ics.IcsProblem` so the
    CLI's ``record.problem`` stays one vocabulary. The rest are the failures only
    a feed parse can have, and they are kept apart because "0 events" from each
    of them means something different to a human at 09:00.
    """

    NONE = "none"
    TRANSPORT = "transport"
    NOT_MODIFIED = "not_modified"
    EMPTY_BODY = "empty_body"
    WRONG_CONTENT_TYPE = "wrong_content_type"

    #: Non-2xx on the registered URL — **including a 404 carrying a perfectly
    #: good feed**. See the module docstring: status and body disagreed, and the
    #: disagreement is the finding.
    HTTP_ERROR = "http_error"
    #: The body is not an RSS/Atom/RDF document, whatever the header said. The
    #: Crucible's 1.35 MB HTML page under ``application/rss+xml`` lands here.
    NOT_FEED = "not_feed"
    #: XML that would not parse at all, even in recovery mode.
    UNPARSEABLE = "unparseable"
    #: A feed, parsed, with zero ``<item>`` / ``<entry>`` elements. The markup
    #: drifted or the feed emptied; either way it wants a human, and it is *not*
    #: the same as a change signal.
    NO_ITEMS = "no_items"
    #: ``events`` mode, items present, and not one carrying a usable date. A
    #: schema change, not a quiet week.
    NO_DATES = "no_dates"


# --------------------------------------------------------------------------- items


@dataclass(frozen=True)
class FeedItem:
    """One ``<item>`` or ``<entry>``, exactly as the feed wrote it.

    Deliberately pre-interpretation: :attr:`date` is parsed but **nothing has
    decided what it means yet**. That decision is the registry's
    (``pubdate_means``) and it happens in :func:`_build_events`, which is what
    lets a test assert the parse separately from the interpretation.
    """

    title: str | None = None
    link: str | None = None
    #: ``<guid>`` / Atom ``<id>`` / ``rdf:about``. The feed's own stable
    #: identity, and therefore the UID.
    guid: str | None = None
    description: str | None = None
    categories: tuple[str, ...] = ()
    author: str | None = None

    #: The date string verbatim: ``"Thu, 06 Aug 2026 18:00:00 -0700"``.
    date_text: str | None = None
    #: Parsed. Aware unless the feed wrote ``-0000`` (RFC 5322's "local time
    #: unknown"), in which case it is naive and :attr:`date_form` says
    #: ``"floating"`` — normalize.py's floating policy owns it from there.
    date: dt.datetime | None = None
    #: ``"offset"`` | ``"utc"`` | ``"floating"`` | ``"unknown"``.
    date_form: str = "unknown"
    #: Which element it came from: ``"pubDate"``, ``"published"``, ``"updated"``,
    #: ``"dc:date"``.
    date_tag: str | None = None

    @property
    def identity(self) -> str:
        """The most stable handle this item offers. Never a scrape timestamp."""
        return self.guid or self.link or self.title or ""

    @property
    def dated(self) -> bool:
        return self.date is not None


@dataclass(frozen=True)
class Feed:
    """One parsed feed document: the channel, and its items in feed order."""

    #: ``"rss"`` | ``"atom"`` | ``"rdf"``. Empty when the body was not a feed.
    flavor: str = ""
    title: str | None = None
    link: str | None = None
    description: str | None = None
    language: str | None = None
    #: ``<lastBuildDate>`` / Atom ``<updated>`` — the channel's own statement
    #: about when it last changed. The only date an ``event_start`` feed may be
    #: judged stale by.
    updated: dt.datetime | None = None
    updated_text: str | None = None
    items: tuple[FeedItem, ...] = ()

    @property
    def item_count(self) -> int:
        return len(self.items)


# --------------------------------------------------------------------------- liveness


@dataclass(frozen=True)
class RssLiveness:
    """The change-detection signal. **The thing a change-only feed is for.**

    A count-based gate cannot tell a live announcement feed from one that stopped
    six months ago — both report zero events forever. This is what it reads
    instead: how many items, how old the newest one is, and a digest that detects
    change without needing dates at all.
    """

    item_count: int = 0
    #: Newest item date, in whatever ``pubDate`` means for this feed.
    newest_item_at: dt.datetime | None = None
    oldest_item_at: dt.datetime | None = None
    #: Channel ``lastBuildDate`` / Atom ``<updated>``, when the feed gave one.
    feed_updated_at: dt.datetime | None = None
    #: **True when the item dates above are post dates**, i.e. when they can be
    #: read as freshness at all. False for Sudo Room's ``event_start`` feed,
    #: whose newest item is a date in the future.
    dates_are_post_dates: bool = True
    newest_title: str | None = None
    #: ``sha1`` over the items' own identities. Changes when the feed changes,
    #: and needs no dates — which is the point, since a feed that stops dating
    #: its posts has not stopped changing. Never includes a fetch timestamp.
    digest: str = ""
    observed_at: dt.datetime | None = None

    @property
    def age_days(self) -> float | None:
        """Days since the newest post, or ``None`` when that is not knowable.

        ``None`` for an ``event_start`` feed rather than a negative number: the
        honest answer to "how stale is this" is "these dates do not say".
        """
        moment = self.newest_item_at if self.dates_are_post_dates else None
        moment = moment or self.feed_updated_at
        if moment is None or self.observed_at is None:
            return None
        return (self.observed_at - moment).total_seconds() / 86400.0

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready, for ``health.json`` and for the run report."""
        return {
            "item_count": self.item_count,
            "newest_item_at": _iso(self.newest_item_at),
            "oldest_item_at": _iso(self.oldest_item_at),
            "feed_updated_at": _iso(self.feed_updated_at),
            "dates_are_post_dates": self.dates_are_post_dates,
            "newest_title": self.newest_title,
            "digest": self.digest,
            "age_days": round(self.age_days, 3) if self.age_days is not None else None,
        }

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        age = f"{self.age_days:.1f}d old" if self.age_days is not None else "age unknown"
        return f"{self.item_count} items, {age}, digest {self.digest}"


def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


# --------------------------------------------------------------------------- events


@dataclass(frozen=True)
class RssEvent(IcsEvent):
    """One item read as an event, in the intermediate shape ``ics`` produces.

    A subclass rather than a parallel class, for the reason every adapter here
    subclasses it: ``normalize.py`` consumes every adapter through
    :func:`~pipeline.normalize.from_ics_event`, and the moment a second shape
    appears one of them starts quietly losing fields.

    :attr:`~pipeline.adapters.ics.IcsEvent.end` is always ``None``. Sudo Room's
    RSS carries no end time and no venue, which is exactly why it sits at
    ``trust: 40`` behind the same space's ICS feed at 100 — and inventing a
    duration would render a wrong block in every calendar client.
    """

    #: The ``<guid>`` verbatim. The UID, before normalize namespaces it.
    guid: str | None = None
    #: The date string as the feed wrote it. Provenance for a value whose whole
    #: meaning came from the registry.
    pubdate_text: str | None = None
    #: Which element the date came from.
    pubdate_tag: str | None = None
    #: The channel title, so an event can be traced to the document it came from.
    feed_title: str | None = None


# --------------------------------------------------------------------------- result


@dataclass(frozen=True)
class RssParse(IcsParse):
    """The adapter's return value. Same surface as :class:`IcsParse`, plus the
    feed, the mode it was read in, and the liveness signal.

    Subclasses :class:`~pipeline.adapters.ics.IcsParse` so every consumer written
    against the ICS adapter — ``normalize_ics``, the CLI's ``SourceRecord``,
    issue 0016's gates — works unchanged.
    """

    problem: RssProblem = RssProblem.NONE  # type: ignore[assignment]

    # -- the declaration -------------------------------------------------------
    #: What the registry said ``pubDate`` means here.
    pubdate_means: PubDate = PubDate.POST_DATE
    #: What the registry said this feed is for.
    mode: RssMode = DEFAULT_MODE
    #: **False when no registry entry supplied a declaration.** The mode then
    #: fell back to change detection, which publishes nothing — and this flag is
    #: how that shows up as a decision rather than as an empty calendar.
    declared: bool = False

    # -- the document ----------------------------------------------------------
    #: ``"rss"`` | ``"atom"`` | ``"rdf"``, or ``""`` when the body was not a feed.
    flavor: str = ""
    feed_title: str | None = None
    feed_link: str | None = None
    #: ``<item>`` / ``<entry>`` elements found, before any interpretation.
    item_count: int = 0
    #: Items carrying a usable date.
    dated_item_count: int = 0
    #: Items with no usable date. In ``events`` mode these are dropped and
    #: counted; in the other modes they are unremarkable.
    undated_item_count: int = 0
    #: ``sha1(body)[:16]``, for diffing two files out of ``raw/``.
    body_digest: str = ""
    body_bytes: int = 0

    # -- the three facts, kept apart --------------------------------------------
    http_status: int | None = None
    declared_content_type: str | None = None
    #: What the **bytes** were: ``"rss"`` | ``"atom"`` | ``"rdf"`` | ``"html"`` |
    #: ``"unknown"``. Independent of the header, on purpose.
    body_shape: str = "unknown"
    #: Every contradiction found between status, content type and parse result,
    #: verbatim. Populated even on a success — a tolerated ``text/plain`` is
    #: recorded rather than forgotten.
    disagreements: tuple[str, ...] = ()

    # -- the two non-event outputs ----------------------------------------------
    #: The liveness signal. ``None`` when there was nothing to observe.
    liveness: RssLiveness | None = None
    #: Item ``<link>`` URLs in feed order, for another adapter to follow.
    #: Populated in ``seed_list`` mode.
    seed_urls: tuple[str, ...] = ()
    #: Links deliberately left out of :attr:`seed_urls`, with the reason: off
    #: host, or a robots-disallowed ``?format=`` route.
    skipped_seeds: tuple[tuple[str, str], ...] = ()

    @property
    def ok(self) -> bool:
        """True when the body was a feed and we read it.

        Zero events with ``ok=True`` is the *normal* answer for two of the three
        registered consumers, and it is not a failure — see
        :attr:`reported_change_signal`.
        """
        return self.problem is RssProblem.NONE

    @property
    def parsed(self) -> bool:
        """A real feed document was found and decoded, event count aside."""
        return self.ok and bool(self.flavor) and self.item_count > 0

    @property
    def emits_events(self) -> bool:
        """True only when the registry declared this feed an event source."""
        return self.mode is RssMode.EVENTS

    @property
    def reported_change_signal(self) -> bool:
        """**The change-detection verdict.** Zero events, and that is correct.

        The distinction the module docstring insists on, as one boolean: true
        means the feed was found, parsed and read and is deliberately not a
        calendar. :attr:`RssProblem.NO_ITEMS` — parsed and found nothing — is
        ``ok=False`` and can never be confused with it.
        """
        return self.parsed and not self.emits_events and self.liveness is not None

    @property
    def is_seed_list(self) -> bool:
        return self.mode is RssMode.SEED_LIST

    @property
    def seed_count(self) -> int:
        return len(self.seed_urls)

    def as_signal(self) -> dict[str, Any]:
        """The liveness signal as ``health.json``-ready data.

        The numbers also reach ``health.json`` through the fields the CLI already
        records — ``raw_count`` is :attr:`item_count`, and ``last_change`` /
        ``stale_days`` come from :attr:`~pipeline.adapters.ics.IcsParse.last_change`
        — so this is the whole picture in one place rather than a second source of
        truth.
        """
        return {
            "mode": self.mode.value,
            "pubdate_means": self.pubdate_means.value,
            "declared": self.declared,
            "flavor": self.flavor,
            "feed_title": self.feed_title,
            "item_count": self.item_count,
            "event_count": self.event_count,
            "emits_events": self.emits_events,
            "change_signal": self.reported_change_signal,
            "seed_urls": list(self.seed_urls),
            "body_digest": self.body_digest,
            "liveness": self.liveness.as_dict() if self.liveness else None,
            "disagreements": list(self.disagreements),
        }

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        head = f"{self.space_id}:{self.label}" if self.space_id or self.label else "<rss>"
        if not self.ok:
            return f"{head} FAILED {self.problem.value}: {self.error}"
        if self.is_seed_list:
            return f"{head} seed list: {self.seed_count} of {self.item_count} item link(s)"
        if not self.emits_events:
            return f"{head} change signal only: {self.liveness or 'nothing observed'}"
        return (
            f"{head} {self.event_count} events in {self.horizon_days}d "
            f"(from {self.item_count} {self.flavor} item(s))"
        )


# --------------------------------------------------------------------------- sniffing


def looks_like_feed(text: str) -> bool:
    """True when the body **opens as** an RSS, Atom or RDF document.

    Reads the bytes, never the header. This is the half of the policy that keeps
    The Crucible's 1.35 MB HTML page out of the calendar even though the response
    declared ``application/rss+xml``.
    """
    return body_shape(text) in ("rss", "atom", "rdf")


def looks_like_html(text: str) -> bool:
    return body_shape(text) == "html"


def body_shape(text: str) -> str:
    """``"rss"`` | ``"atom"`` | ``"rdf"`` | ``"html"`` | ``"unknown"``.

    What the document *is*, decided from its first bytes and from nothing else.
    An XML declaration is skipped over, because ``<?xml …?><rss>`` and ``<rss>``
    are the same document.
    """
    head = text[:_SNIFF_BYTES].lstrip("﻿ \t\r\n")
    lowered = head.lower()
    if lowered.startswith(("<!doctype html", "<html", "<head", "<body")):
        return "html"
    # Skip an XML declaration, any processing instructions and any comments, so
    # the first *element* is what decides.
    cursor = 0
    while cursor < len(lowered):
        if lowered.startswith("<?", cursor):
            end = lowered.find("?>", cursor)
            if end == -1:
                break
            cursor = end + 2
        elif lowered.startswith("<!--", cursor):
            end = lowered.find("-->", cursor)
            if end == -1:
                break
            cursor = end + 3
        elif lowered.startswith("<!doctype", cursor):
            end = lowered.find(">", cursor)
            if end == -1:
                break
            if "html" in lowered[cursor:end]:
                return "html"
            cursor = end + 1
        elif lowered[cursor] in " \t\r\n":
            cursor += 1
        else:
            break
    rest = lowered[cursor:]
    for opener, shape in (
        ("<rss", "rss"),
        ("<feed", "atom"),
        ("<rdf", "rdf"),
        ("<html", "html"),
        ("<!doctype html", "html"),
    ):
        if rest.startswith(opener):
            return shape
    return "unknown"


# --------------------------------------------------------------------------- dates


@dataclass(frozen=True)
class _Moment:
    """One parsed feed date, with how it was written."""

    value: dt.datetime | None = None
    #: ``"offset"`` | ``"utc"`` | ``"floating"`` | ``"unknown"``.
    form: str = "unknown"
    offset: str | None = None


def parse_feed_date(text: Any) -> _Moment:
    """Parse an RSS or Atom date, **keeping the offset it was written with**.

    RSS 2.0 dates are RFC 822/5322 (``"Thu, 06 Aug 2026 18:00:00 -0700"``, and
    the obsolete named forms ``PDT``/``GMT`` do occur), so the stdlib email
    parser goes first — it is the parser the format was specified for. Atom and
    ``dc:date`` are RFC 3339, read with ``isoparse``. ``dateutil`` is the last
    resort for producers who split the difference.

    The exact instant the feed stated is preserved: no conversion to UTC and no
    conversion to a named zone. That happens once, in ``normalize.py``.

    ``-0000`` means "local time unknown" in RFC 5322 and parses to a **naive**
    datetime, reported as ``form="floating"``. It is passed on naive on purpose:
    stamping a zone on it here would be the silent guess the no-naive-datetime
    invariant exists to prevent, and ``normalize.py`` has a stated, logged and
    counted policy for floating times.

    Never raises. An unparseable value yields an empty :class:`_Moment` and the
    item is counted as undated.
    """
    value = clean(text)
    if not value:
        return _Moment()

    parsed: dt.datetime | None = None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        parsed = None
    if parsed is None:
        try:
            parsed = isoparse(value)
        except (TypeError, ValueError, OverflowError):
            parsed = None
    if parsed is None:
        try:
            from dateutil.parser import parse as loose_parse

            parsed = loose_parse(value)
        except (TypeError, ValueError, OverflowError):
            return _Moment()

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        # RFC 5322's "-0000": the producer is explicitly declining to say. Naive,
        # flagged floating, and normalize.py's policy takes it from here.
        return _Moment(value=parsed.replace(tzinfo=None), form="floating")

    offset = parsed.utcoffset() or dt.timedelta(0)
    aware = parsed.replace(tzinfo=dt.timezone(offset))
    form = "utc" if offset == dt.timedelta(0) else "offset"
    return _Moment(value=aware, form=form, offset=offset_text(aware))


# --------------------------------------------------------------------------- parsing


def _localname(tag: Any) -> str:
    """The element name without its namespace, lowercased.

    Namespace-agnostic on purpose. These feeds carry ``content:``, ``media:``,
    ``dc:``, ``slash:`` and ``atom:`` prefixes in every combination, and matching
    on the local name is what keeps this from becoming a per-vendor special case.
    """
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(element: Any, *names: str) -> tuple[str | None, str | None]:
    """``(text, which name matched)`` for the first direct child that has text."""
    for name in names:
        for child in element:
            if _localname(child.tag) != name:
                continue
            text = clean(child.xpath("string()"))
            if text:
                return text, name
    return None, None


def _item_link(element: Any, *, base_url: str = "") -> str | None:
    """The item's canonical link.

    Atom writes ``<link href="…" rel="alternate">`` (and ``rel`` defaults to
    ``alternate`` when absent); RSS writes ``<link>text</link>``. Both are read,
    and anything with another ``rel`` — ``self``, ``enclosure``, ``replies`` — is
    skipped, because a seed list of ``rel="self"`` URLs would have every adapter
    downstream re-fetching the feed.
    """
    for child in element:
        if _localname(child.tag) != "link":
            continue
        rel = (child.get("rel") or "alternate").lower()
        if rel != "alternate":
            continue
        href = (child.get("href") or child.text or "").strip()
        if not href:
            continue
        return urljoin(base_url, href) if base_url else href
    return None


def _categories(element: Any) -> tuple[str, ...]:
    """``<category>`` values: RSS puts them in the text, Atom in ``@term``."""
    out: list[str] = []
    for child in element:
        if _localname(child.tag) != "category":
            continue
        text = clean(child.get("term")) or clean(child.xpath("string()"))
        if text and text not in out:
            out.append(text)
    return tuple(out)


def _guid(element: Any, *, link: str | None) -> str | None:
    """``<guid>`` / Atom ``<id>`` / ``rdf:about``, falling back to the link.

    The feed's own stable identity, which is the entire requirement for a UID:
    if it churns, every subscriber sees every event as new every night.
    """
    text, _tag = _child_text(element, "guid", "id")
    if text:
        return text
    about = element.get("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about")
    return clean(about) or link


def _parse_item(element: Any, *, base_url: str = "") -> FeedItem:
    title, _ = _child_text(element, "title")
    link = _item_link(element, base_url=base_url)
    date_text, date_tag = _child_text(element, *DATE_TAGS)
    moment = parse_feed_date(date_text)
    description, _ = _child_text(element, "description", "summary", "content", "encoded")
    author, _ = _child_text(element, "author", "creator", "name")
    return FeedItem(
        title=title,
        link=link,
        guid=_guid(element, link=link),
        description=description,
        categories=_categories(element),
        author=author,
        date_text=date_text,
        date=moment.value,
        date_form=moment.form,
        date_tag=date_tag,
    )


def parse_feed(document: str, *, base_url: str = "") -> Feed | None:
    """One RSS/Atom/RDF document → a :class:`Feed`, or ``None`` if it is not one.

    ``None`` covers both halves of the trap: a body that is HTML (whatever the
    header claimed) and XML that carries no ``<rss>``/``<feed>``/``<rdf>`` root.
    Parsed in recovery mode, because a single bad entity in item 900 of 1715 must
    not cost the other 1714.
    """
    shape = body_shape(document)
    if shape not in ("rss", "atom", "rdf", "unknown"):
        return None
    try:
        parser = lxml.etree.XMLParser(
            recover=True, resolve_entities=False, no_network=True, huge_tree=False
        )
        tree = lxml.etree.fromstring(document.encode("utf-8"), parser=parser)
    except (lxml.etree.XMLSyntaxError, ValueError):
        return None
    if tree is None:
        return None

    root = _localname(tree.tag)
    if root not in ("rss", "feed", "rdf"):
        return None
    flavor = {"rss": "rss", "feed": "atom", "rdf": "rdf"}[root]

    # RSS and RDF hang the metadata off <channel>; Atom puts it on the root.
    channel = tree
    for child in tree:
        if _localname(child.tag) == "channel":
            channel = child
            break

    title, _ = _child_text(channel, "title")
    description, _ = _child_text(channel, "description", "subtitle")
    language, _ = _child_text(channel, "language")
    updated_text, _ = _child_text(channel, *FEED_DATE_TAGS)
    updated = parse_feed_date(updated_text)

    items = tuple(
        _parse_item(element, base_url=base_url)
        for element in tree.iter()
        if _localname(element.tag) in ITEM_TAGS
    )

    return Feed(
        flavor=flavor,
        title=title,
        link=_item_link(channel, base_url=base_url),
        description=description,
        language=language,
        updated=updated.value,
        updated_text=updated_text,
        items=items,
    )


def item_links(document: str, *, base_url: str = "") -> tuple[str, ...]:
    """Item ``<link>`` URLs, in feed order, deduplicated.

    **This is not a crawl.** It reads the links a registered feed offers and
    nothing else — no ``<a href>``, no sitemap, no following anything a page
    happens to mention. Deliberately the same rule
    :func:`pipeline.adapters.jsonld.seed_links` applies, because ``jsonld``
    consumes exactly this list for The Box Shop and the two must not drift.
    """
    feed = parse_feed(document, base_url=base_url)
    if feed is None:
        return ()
    out: list[str] = []
    for item in feed.items:
        if item.link and item.link not in out:
            out.append(item.link)
    return tuple(out)


# --------------------------------------------------------------------------- building


def _sort_key(event: RssEvent) -> tuple[dt.date, int, dt.time, str]:
    """Same ordering rule as the ICS adapter: day, all-day first, then time."""
    start = event.start
    if isinstance(start, dt.datetime):
        moment = start.astimezone(dt.timezone.utc) if start.tzinfo else start
        return (moment.date(), 1, moment.time(), event.uid or "")
    return (start, 0, dt.time.min, event.uid or "")


def _build_events(feed: Feed) -> tuple[RssEvent, ...]:
    """Items → events, reading each item's date as the start.

    Only ever called when the registry declared ``pubdate_means: event_start``.
    Everything about that reading is the declaration's; this function does the
    mechanical part and nothing else.
    """
    events: list[RssEvent] = []
    for item in feed.items:
        if item.date is None:
            continue
        aware = item.date.tzinfo is not None
        events.append(
            RssEvent(
                # The feed's own <guid>. normalize.py namespaces it to
                # {space_id}:{guid}; the item is not a recurring series, so the
                # two-part form is correct and the UID is stable across runs by
                # construction.
                uid=item.guid,
                title=item.title,
                start=item.date,
                # No end, ever: the feed carries none. See RssEvent.
                end=None,
                all_day=False,
                multi_day=False,
                days=1,
                # A numeric offset is not a zone, and everything downstream calls
                # ZoneInfo on this — including issue 0016's health filter, which
                # runs outside the per-source try/except. The instant stays
                # exactly what the feed wrote; this only supplies the IANA name
                # to render it in. A floating date carries no zone at all and
                # normalize.py's floating policy interprets it.
                tz=DEFAULT_ZONE if aware else None,
                source_tz=None,
                dtstart_form=item.date_form,
                # RSS carries no venue. Left empty so VenuePolicy (issue 0009)
                # applies the space's address_override, which is the right answer
                # for Sudo Room and the reason its address lives in the registry.
                location=None,
                description=item.description,
                url=item.link,
                categories=item.categories,
                status=None,
                organizer=item.author,
                # RSS has no recurrence rule. Each item is one occurrence.
                recurring=False,
                last_modified=None,
                dtstamp=None,
                guid=item.guid,
                pubdate_text=item.date_text,
                pubdate_tag=item.date_tag,
                feed_title=feed.title,
            )
        )
    return tuple(events)


def _liveness(
    feed: Feed, *, pubdate_means: PubDate, declared: bool, now: dt.datetime
) -> RssLiveness | None:
    """The change signal for one feed, or ``None`` when there is nothing to see."""
    if not feed.items:
        return None
    # Aware values only. A feed may mix ``-0000`` (naive by RFC 5322) with real
    # offsets in the same document — Sudo Room's does — and comparing the two
    # raises rather than being wrong quietly. The naive ones are still parsed and
    # still become events; they simply cannot take part in "which is newest".
    dates = sorted(
        item.date
        for item in feed.items
        if item.date is not None and item.date.tzinfo is not None
    )
    newest = dates[-1] if dates else None
    newest_item = None
    for item in feed.items:
        if item.date is not None and item.date == newest:
            newest_item = item
            break
    material = "\n".join(item.identity for item in feed.items)
    return RssLiveness(
        item_count=feed.item_count,
        newest_item_at=newest,
        oldest_item_at=dates[0] if dates else None,
        feed_updated_at=feed.updated,
        # The one thing that makes an age number honest. An *undeclared* source
        # gets False as well as an ``event_start`` one: "nobody said what these
        # dates mean" and "these dates are in the future" both make an age
        # meaningless, and inventing one from an undeclared feed would be the
        # same guess this module exists to refuse.
        dates_are_post_dates=declared and pubdate_means is PubDate.POST_DATE,
        newest_title=(newest_item.title if newest_item else feed.items[0].title),
        digest=hashlib.sha1(material.encode("utf-8")).hexdigest()[:16],
        observed_at=now,
    )


def _seed_urls(
    feed: Feed, *, base_url: str
) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    """``(kept, skipped)`` item links for a seed list.

    Two links never make it into a seed list, and neither omission is silent:
    one carrying a robots-disallowed ``?format=`` value, and one pointing at
    another host. "Fetch only the URLs in ``sources.yaml``" survives a two-step
    flow only if step two stays on the site step one came from.
    """
    try:
        seed_host = (httpx.URL(base_url).host or "").lower()
    except (ValueError, httpx.InvalidURL):  # pragma: no cover - defensive
        seed_host = ""

    kept: list[str] = []
    skipped: list[tuple[str, str]] = []
    for item in feed.items:
        link = item.link
        if not link:
            continue
        if link in kept or any(link == url for url, _why in skipped):
            continue
        if is_disallowed_route(link):
            skipped.append((link, "robots.txt disallows this ?format= route"))
            continue
        try:
            host = (httpx.URL(link).host or "").lower()
        except (ValueError, httpx.InvalidURL):
            skipped.append((link, "not a usable URL"))
            continue
        if seed_host and host and host != seed_host:
            skipped.append((link, f"off host ({host})"))
            continue
        kept.append(link)
    return tuple(kept), tuple(skipped)


def _staleness(
    feed: Feed, *, pubdate_means: PubDate, declared: bool
) -> tuple[dt.datetime | None, dt.datetime | None]:
    """``(last_modified, dtstamp)`` — what ``max_stale_days`` may read.

    The channel's own ``lastBuildDate`` always counts. Item dates count **only**
    when the registry declared them post dates: Sudo Room's are event starts in
    the future, and reporting a feed as fresh because it announces something next
    March is the same error as publishing an announcement date as a start. An
    undeclared feed contributes no item dates here either, for the same reason.
    """
    if declared and pubdate_means is PubDate.POST_DATE:
        dates = [
            item.date
            for item in feed.items
            if item.date is not None and item.date.tzinfo is not None
        ]
        newest = max(dates) if dates else None
        return (feed.updated or newest, newest)
    return (feed.updated, None)


def _declaration(
    ref: SourceRef | None,
    pubdate_means: PubDate | str | None,
    mode: RssMode | str | None,
) -> tuple[PubDate, RssMode, bool]:
    """``(pubdate_means, mode, declared)``. **Never guesses in favour of events.**

    An explicit argument wins (that is how a test states the case it is testing),
    then the registry entry, and then the safe default: ``post_date`` read in
    ``change_detection`` mode, with ``declared=False`` recording that nobody
    said. The fallback publishes nothing, which is the only defensible default
    for a feed whose dates might mean either thing.
    """
    raw_means = pubdate_means
    raw_mode = mode
    if raw_means is None and ref is not None:
        raw_means = ref.source.pubdate_means
    if raw_mode is None and ref is not None:
        raw_mode = ref.source.rss_mode

    declared = raw_means is not None
    try:
        means = PubDate(raw_means) if raw_means is not None else PubDate.POST_DATE
    except ValueError:  # pragma: no cover - the loader rejects these first
        means, declared = PubDate.POST_DATE, False
    try:
        resolved = RssMode(raw_mode) if raw_mode is not None else DEFAULT_MODE
    except ValueError:  # pragma: no cover - the loader rejects these first
        resolved = DEFAULT_MODE
    if resolved is RssMode.EVENTS and means is not PubDate.EVENT_START:
        # The loader forbids this pairing; if it ever arrives anyway, the safe
        # reading is the one that publishes nothing.
        resolved = DEFAULT_MODE
    return means, resolved, declared


# --------------------------------------------------------------------------- decoding


def _transport_problem(result: FetchResult, *, where: str) -> tuple[RssProblem, str] | None:
    """Everything that can be wrong before the body is worth looking at.

    Note what is **not** here: a non-2xx status. That check needs the parse
    result to describe itself properly — "404 carrying a valid RSS feed with 12
    items" is a different report from "404 carrying an HTML error page" — so it
    happens in :func:`_decode`, after the body has been read.
    """
    if result.outcome is Outcome.NOT_MODIFIED:
        return (
            RssProblem.NOT_MODIFIED,
            f"{where}: HTTP 304, unchanged since the last run — reuse the stored "
            "events rather than treating this as zero",
        )
    if result.outcome is not Outcome.FETCHED:
        detail = f" ({result.reason})" if result.reason else ""
        return (RssProblem.TRANSPORT, f"{where}: {result.outcome.value}{detail}")
    if not result.has_body:
        return (
            RssProblem.EMPTY_BODY,
            f"{where}: HTTP {result.status_code} with an empty body",
        )
    return None


def _build(
    feed: Feed | None,
    events: Sequence[RssEvent],
    *,
    problem: RssProblem = RssProblem.NONE,
    error: str | None = None,
    pubdate_means: PubDate,
    mode: RssMode,
    declared: bool,
    space_id: str,
    label: str,
    source_url: str,
    horizon_days: int,
    window_start: dt.date | None,
    window_end: dt.date | None,
    now: dt.datetime,
    document: str = "",
    http_status: int | None = None,
    declared_content_type: str | None = None,
    disagreements: Sequence[str] = (),
    liveness: RssLiveness | None = None,
    seed_urls: Sequence[str] = (),
    skipped_seeds: Sequence[tuple[str, str]] = (),
) -> RssParse:
    """Assemble one parse into the returned :class:`RssParse`.

    Horizon clipping happens here, on the event's own local day, so the count
    this reports is the post-clip one issue 0016's gates read.
    """
    kept: list[RssEvent] = []
    for event in events:
        start = event.start
        day = start.date() if isinstance(start, dt.datetime) else start
        if window_start is None or window_end is None or window_start <= day <= window_end:
            kept.append(event)

    dated = sum(1 for item in (feed.items if feed else ()) if item.dated)
    last_modified, dtstamp = (
        _staleness(feed, pubdate_means=pubdate_means, declared=declared)
        if feed
        else (None, None)
    )
    body = document.encode("utf-8")

    return RssParse(
        events=tuple(sorted(kept, key=_sort_key)),
        problem=problem,
        error=error,
        space_id=space_id,
        label=label,
        source_url=source_url,
        calendar_name=feed.title if feed else None,
        calendar_description=feed.description if feed else None,
        last_modified=last_modified,
        dtstamp=dtstamp,
        # Raw = items in the document. The health gates count `event_count`,
        # which is the post-clip number — and for a change-detection feed it is
        # zero by design while `raw_count` still shows the feed is alive.
        vevent_count=feed.item_count if feed else 0,
        recurring_vevent_count=0,
        horizon_days=horizon_days,
        window_start=window_start,
        window_end=window_end,
        parsed_at=now,
        pubdate_means=pubdate_means,
        mode=mode,
        declared=declared,
        flavor=feed.flavor if feed else "",
        feed_title=feed.title if feed else None,
        feed_link=feed.link if feed else None,
        item_count=feed.item_count if feed else 0,
        dated_item_count=dated,
        undated_item_count=(feed.item_count - dated) if feed else 0,
        body_digest=hashlib.sha1(body).hexdigest()[:16] if body else "",
        body_bytes=len(body),
        http_status=http_status,
        declared_content_type=declared_content_type,
        body_shape=body_shape(document) if document else "unknown",
        disagreements=tuple(disagreements),
        liveness=liveness,
        seed_urls=tuple(seed_urls),
        skipped_seeds=tuple(skipped_seeds),
    )


def _decode(
    document: str,
    *,
    where: str,
    pubdate_means: PubDate,
    mode: RssMode,
    declared: bool,
    space_id: str,
    label: str,
    source_url: str,
    horizon_days: int,
    window_start: dt.date,
    window_end: dt.date,
    now: dt.datetime,
    http_status: int | None = None,
    declared_content_type: str | None = None,
    content_type_ok: bool = True,
    content_type_lenient: bool = False,
) -> RssParse:
    """Read one feed document. Never raises.

    **The three facts are collected before any of them is allowed to decide.**
    The body is parsed first — even under a 404 — so the report can name what was
    actually there; then status, content type and parse result are compared, and
    any disagreement is a reported failure. See the module docstring for why the
    status wins on Ace's 404-with-a-good-feed and why the header never wins on
    The Crucible's 404-with-HTML.
    """
    shape = body_shape(document)
    feed = parse_feed(document, base_url=source_url)
    status_ok = http_status is None or 200 <= http_status < 300
    disagreements: list[str] = []

    def outcome(
        problem: RssProblem,
        error: str | None,
        *,
        liveness: RssLiveness | None = None,
        seed_urls: Sequence[str] = (),
        skipped_seeds: Sequence[tuple[str, str]] = (),
        events: Sequence[RssEvent] = (),
    ) -> RssParse:
        return _build(
            feed,
            events,
            problem=problem,
            error=error,
            pubdate_means=pubdate_means,
            mode=mode,
            declared=declared,
            space_id=space_id,
            label=label,
            source_url=source_url,
            horizon_days=horizon_days,
            window_start=window_start,
            window_end=window_end,
            now=now,
            document=document,
            http_status=http_status,
            declared_content_type=declared_content_type,
            disagreements=disagreements,
            liveness=liveness,
            seed_urls=seed_urls,
            skipped_seeds=skipped_seeds,
        )

    # --- fact 1: what the body is --------------------------------------------
    body_is_feed = feed is not None
    if not body_is_feed:
        disagreements.append(
            f"the body is {shape!r}, not a feed"
            + (
                f", while the response declared {declared_content_type!r}"
                if declared_content_type
                else ""
            )
        )

    # --- fact 2: what the header said ----------------------------------------
    if not content_type_ok:
        disagreements.append(
            f"content type {declared_content_type!r} is not an XML type"
            + (
                " — tolerated, because the body is a real feed and some hosts serve "
                "one as text/plain"
                if content_type_lenient and body_is_feed
                else ""
            )
        )

    # --- fact 3: what the status said ----------------------------------------
    if not status_ok:
        disagreements.append(
            f"HTTP {http_status} carrying "
            + (
                f"a valid {feed.flavor} feed with {feed.item_count} item(s)"
                if feed is not None
                else f"a {shape} body"
            )
        )

    # --- the verdicts, in the order the policy states them --------------------
    if not status_ok:
        # **Trap 1, and the deliberate decision.** Ace's /calendar/feed/ answers
        # 404 with a populated, valid 9.8 KB RSS body. The status wins: an
        # endpoint the server says does not exist is not a published feed, it can
        # change or vanish without notice, and the canonical events RSS for that
        # site is elsewhere (?post_type=tribe_events&feed=rss2). Failing here also
        # means issue 0014 republishes yesterday's events instead of tonight's
        # nothing, which is the recoverable direction. The body is parsed anyway,
        # above, so the report says exactly what was in it.
        return outcome(
            RssProblem.HTTP_ERROR,
            f"{where}: HTTP {http_status}, and the body "
            + (
                f"is a valid {feed.flavor} feed with {feed.item_count} item(s). "
                "Status and body disagree, and this adapter refuses to pick the one "
                "that suits it: Ace's /calendar/feed/ is the recorded case (404 with "
                "a populated 9.8 KB RSS body) and its canonical events RSS is a "
                "different URL entirely"
                if feed is not None
                else f"is {shape!r}, not a feed either. Both facts say the same thing"
            ),
        )

    if not body_is_feed:
        # **Trap 2.** The Crucible's /category/<bogus>/feed/ answers with
        # Content-Type: application/rss+xml over 1.35 MB of HTML. The header does
        # not get a vote: the bytes are what they are. This is also the check
        # that stops a login wall or an error page being read as a feed with
        # nothing in it.
        return outcome(
            RssProblem.NOT_FEED,
            f"{where}: the body is {shape!r} and not an RSS/Atom/RDF document"
            + (
                f", although the response declared {declared_content_type!r}. "
                "The header lies here — The Crucible's bogus-category feed is the "
                "recorded case: 404, application/rss+xml, and 1.35 MB of HTML error "
                "page. Content type is asserted *and* the parse result is, and "
                "disagreement is a failure"
                if declared_content_type
                and _looks_like_feed_type(declared_content_type)
                else ". HTTP 200 is not success, and a body that is not the feed we "
                "registered must not be read as a feed with nothing in it"
            ),
        )

    assert feed is not None  # narrowed above

    if not content_type_ok and not content_type_lenient:
        # A real feed under a type that is neither XML nor one of the sloppy-but-
        # honest fallbacks. The body agrees with us and the header does not, and
        # picking the one that suits us is exactly what the policy forbids.
        return outcome(
            RssProblem.WRONG_CONTENT_TYPE,
            f"{where}: expected {' or '.join(XML_CONTENT_TYPES)}, got "
            f"{declared_content_type!r} over a body that really is a {feed.flavor} "
            f"feed with {feed.item_count} item(s). Content type and parse result "
            "disagree; this adapter reports the disagreement rather than choosing "
            "the fact it likes",
        )

    if feed.item_count == 0:
        if mode is RssMode.SEED_LIST:
            # An events collection with nothing upcoming. The Crucible's seed
            # feed carries allow_zero for exactly this, and calling it a failure
            # would alert on a quiet month.
            return outcome(
                RssProblem.NONE,
                f"{where}: the feed parsed as {feed.flavor} and carried no items. For "
                "a seed list this is legitimately empty (allow_zero), not a parse "
                "failure",
                seed_urls=(),
            )
        return outcome(
            RssProblem.NO_ITEMS,
            f"{where}: a valid {feed.flavor} document with **zero** <item>/<entry> "
            "elements. This is not a change signal and it is not an empty calendar — "
            "a feed that parsed and carried nothing has either drifted or emptied, "
            "and both want a human",
        )

    liveness = _liveness(feed, pubdate_means=pubdate_means, declared=declared, now=now)

    # --- seed list ------------------------------------------------------------
    if mode is RssMode.SEED_LIST:
        kept, skipped = _seed_urls(feed, base_url=source_url)
        notes = [
            f"{where}: seed list only. {feed.item_count} item(s), {len(kept)} usable "
            f"link(s), and **no events**: pubDate here is the post date, so the dates "
            "come from whatever adapter follows these URLs"
        ]
        if skipped:
            notes.append(
                "; ".join(f"skipped {url} ({why})" for url, why in skipped[:5])
                + ("…" if len(skipped) > 5 else "")
            )
        return outcome(
            RssProblem.NONE,
            "; ".join(notes),
            liveness=liveness,
            seed_urls=kept,
            skipped_seeds=skipped,
        )

    # --- change detection -----------------------------------------------------
    if mode is not RssMode.EVENTS:
        return outcome(
            RssProblem.NONE,
            f"{where}: change detection only, **no events emitted**. "
            f"{feed.item_count} item(s) in a {feed.flavor} feed"
            + (
                ""
                if declared
                else " — and no per-source pubdate_means declaration was supplied, so "
                "the safe default applied. This adapter never guesses that a pubDate "
                "is an event start"
            )
            + f". Liveness: {liveness}",
            liveness=liveness,
        )

    # --- events ---------------------------------------------------------------
    events = _build_events(feed)
    if not events:
        return outcome(
            RssProblem.NO_DATES,
            f"{where}: {feed.item_count} item(s) declared pubdate_means=event_start "
            "and not one carried a usable date. The registry says this feed dates its "
            "events; it has stopped doing so, which is a schema change and not a "
            "quiet week",
        )

    notes: list[str] = []
    undated = feed.item_count - len(events)
    if undated:
        notes.append(
            f"{undated} of {feed.item_count} item(s) carried no usable date and were "
            "skipped"
        )
    floating = sum(1 for event in events if event.tz is None)
    if floating:
        notes.append(
            f"{floating} item(s) wrote '-0000' (RFC 5322 'local time unknown') and are "
            "passed on as floating times for normalize.py's policy to interpret"
        )
    if disagreements:
        notes.extend(disagreements)

    return outcome(
        RssProblem.NONE,
        "; ".join(notes) or None,
        liveness=liveness,
        events=events,
    )


def _looks_like_feed_type(content_type: str) -> bool:
    """True when a declared type claims to be a feed. Used only for the message."""
    lowered = content_type.lower()
    return lowered.endswith("+xml") or lowered in XML_CONTENT_TYPES or "xml" in lowered


def _failure(
    problem: RssProblem,
    error: str,
    *,
    pubdate_means: PubDate = PubDate.POST_DATE,
    mode: RssMode = DEFAULT_MODE,
    declared: bool = False,
    space_id: str = "",
    label: str = "",
    source_url: str = "",
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    window_start: dt.date | None = None,
    window_end: dt.date | None = None,
    now: dt.datetime | None = None,
    http_status: int | None = None,
    declared_content_type: str | None = None,
) -> RssParse:
    """A reported failure. Never raised — see the ``ics`` module docstring."""
    return RssParse(
        problem=problem,
        error=error,
        pubdate_means=pubdate_means,
        mode=mode,
        declared=declared,
        space_id=space_id,
        label=label,
        source_url=source_url,
        horizon_days=horizon_days,
        window_start=window_start,
        window_end=window_end,
        parsed_at=now,
        http_status=http_status,
        declared_content_type=declared_content_type,
    )


# --------------------------------------------------------------------------- parsing


def parse_rss(
    result: FetchResult,
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    today: dt.date | None = None,
    now: dt.datetime | None = None,
    strict_content_type: bool = True,
    ref: SourceRef | None = None,
    pubdate_means: PubDate | str | None = None,
    mode: RssMode | str | None = None,
) -> RssParse:
    """Parse a fetched RSS/Atom feed. Never raises.

    ``result`` is the registered feed, already fetched by the caller. The
    declaration comes from ``ref.source.pubdate_means`` / ``ref.source.rss_mode``
    unless an explicit argument overrides it, which is why this adapter is
    registered ``needs_source=True``: the blob it reads is the same shape either
    way, and only the registry knows whether its dates are events.

    Called with **no** declaration at all, it reads the feed in
    ``change_detection`` mode and sets :attr:`RssParse.declared` to ``False``.
    That default publishes nothing, on purpose.

    A 404 with a valid feed body, a 200 with an HTML body under an
    ``application/rss+xml`` header, a feed with no items, and an event feed that
    stopped dating its items all come back as an :class:`RssParse` with
    ``ok=False`` and a distinct :class:`RssProblem`. A change signal and a seed
    list come back with ``ok=True`` and no events.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    today = today or now.date()
    window_start = today
    window_end = today + dt.timedelta(days=horizon_days)
    where = f"{result.source_key} ({result.url})"
    means, resolved_mode, declared = _declaration(ref, pubdate_means, mode)

    transport = _transport_problem(result, where=where)
    if transport is not None:
        return _failure(
            transport[0],
            transport[1],
            pubdate_means=means,
            mode=resolved_mode,
            declared=declared,
            space_id=result.space_id,
            label=result.label,
            source_url=result.url,
            horizon_days=horizon_days,
            window_start=window_start,
            window_end=window_end,
            now=now,
            http_status=result.status_code,
            declared_content_type=result.content_type,
        )

    # The three facts stay three facts. ``content_type_ok`` is "the header
    # declared XML"; ``content_type_lenient`` is "the header is sloppy in a way
    # this project has actually seen and will tolerate over a real feed body".
    # Neither of them is allowed to stand in for the parse result.
    content_type_lenient = result.content_type_is(*LENIENT_CONTENT_TYPES)
    content_type_ok = (
        not strict_content_type
        or result.content_type is None
        or result.content_type_is(*XML_CONTENT_TYPES)
    )

    return _decode(
        result.text,
        where=where,
        pubdate_means=means,
        mode=resolved_mode,
        declared=declared,
        space_id=result.space_id,
        label=result.label,
        source_url=result.final_url or result.url,
        horizon_days=horizon_days,
        window_start=window_start,
        window_end=window_end,
        now=now,
        http_status=result.status_code,
        declared_content_type=result.content_type,
        content_type_ok=content_type_ok,
        content_type_lenient=content_type_lenient,
    )


def parse_rss_text(
    document: str,
    *,
    space_id: str = "",
    label: str = "",
    source_url: str = "",
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    today: dt.date | None = None,
    now: dt.datetime | None = None,
    pubdate_means: PubDate | str | None = None,
    mode: RssMode | str | None = None,
) -> RssParse:
    """Parse **one feed document**. The half with no HTTP in it.

    Split out for the same reason ``ics`` splits ``parse_ics_text``: the tests,
    and a human diffing two files out of ``raw/``, must be able to reach the
    parser without manufacturing a :class:`~pipeline.fetch.FetchResult`. With no
    status and no header there is only one fact to check — the parse result — and
    an HTML body is still rejected on it.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    today = today or now.date()
    means, resolved_mode, declared = _declaration(None, pubdate_means, mode)
    return _decode(
        document,
        where=f"{space_id}:{label}" if space_id or label else "<rss>",
        pubdate_means=means,
        mode=resolved_mode,
        declared=declared,
        space_id=space_id,
        label=label,
        source_url=source_url,
        horizon_days=horizon_days,
        window_start=today,
        window_end=today + dt.timedelta(days=horizon_days),
        now=now,
    )


# --------------------------------------------------------------------------- fetching


def fetch_rss(
    fetcher: Fetcher,
    ref: SourceRef,
    *,
    horizon_days: int | None = None,
    today: dt.date | None = None,
    now: dt.datetime | None = None,
    conditional: bool = False,
    strict_content_type: bool = True,
) -> RssParse:
    """Fetch a registered feed and parse it. All in one call.

    The counterpart to :func:`pipeline.adapters.nextdata.fetch_nextdata`, and the
    entry point for the repair workflow: one source, one document, no run.

    **Exactly one request goes out.** This adapter never follows a link — a seed
    list is *reported*, and the adapter that consumes it does the fetching
    through the same rate limiter and the same ``raw/`` archive.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    today = today or now.date()
    if horizon_days is None:
        horizon_days = fetcher.registry.horizon_days

    _space, source = ref
    result = fetcher.fetch_url(ref, request_url(source), conditional=conditional)
    return parse_rss(
        result,
        horizon_days=horizon_days,
        today=today,
        now=now,
        strict_content_type=strict_content_type,
        ref=ref,
    )


#: The adapter entry point named by ``sources.yaml``'s ``adapter: rss``.
parse = parse_rss


__all__ = [
    "DATE_TAGS",
    "DEFAULT_MODE",
    "DEFAULT_ZONE",
    "EVENTS_MODE",
    "EVENT_START",
    "FEED_DATE_TAGS",
    "ITEM_TAGS",
    "LENIENT_CONTENT_TYPES",
    "PUBDATE_MEANINGS",
    "RSS_MODES",
    "Feed",
    "FeedItem",
    "PubDate",
    "RssEvent",
    "RssLiveness",
    "RssMode",
    "RssParse",
    "RssProblem",
    "body_shape",
    "fetch_rss",
    "item_links",
    "looks_like_feed",
    "looks_like_html",
    "parse",
    "parse_feed",
    "parse_feed_date",
    "parse_rss",
    "parse_rss_text",
]
