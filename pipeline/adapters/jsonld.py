"""JSON-LD adapter: ``schema.org/Event`` out of ``<script type="application/ld+json">``.

Two registered consumers, and they are not the same shape:

=========================================  ====================================
``ace-makerspace`` / ``calendar-jsonld``    **One page.** ``/calendar/`` carries
                                            a single ld+json block holding a
                                            12-element array of ``Event``, with
                                            offset-aware ``startDate``. Current
                                            list-view page only, which is why it
                                            is ``trust: 50`` behind the REST API
                                            (100) and the ICS export (90).
``the-box-shop`` / ``squarespace-events``   **Two steps.** ``/events?format=rss``
                                            enumerates the ``<link>`` of each
                                            item; each ``/events/<slug>`` page
                                            then carries the real
                                            ``schema.org/Event``. ``trust: 95``.
=========================================  ====================================

Why the two-step flow exists, and why it is not optional
--------------------------------------------------------

**The Box Shop's RSS carries no event date.** ``pubDate`` is when the post was
published — 2026-06-29 for an event on 2026-08-08 — so an adapter that trusted
the feed would publish a calendar of announcement dates, every one of them
wrong, and nothing downstream would ever say so. The per-event page is the only
robots-permitted route to a real date: ``/events/<slug>`` emits
``startDate: 2026-08-08T18:00:00-0700``.

That is why :func:`parse_jsonld` accepts a **seed list** rather than assuming a
single page, and why it takes the ``Fetcher`` through the same seam issue 0019
opened for pagination (:attr:`pipeline.cli.AdapterEntry.paginates`). Every
follow-up GET therefore shares one ``robots.txt`` decision, one rate limiter and
one ``raw/`` archive with the seed fetch. The number of follow-ups is capped at
:data:`MAX_SEED_FETCHES`, because a feed that suddenly lists 400 items is a
change in the source, not a licence to make 400 requests against a volunteer
nonprofit's Squarespace site.

**We never construct a disallowed URL.** ``boxshopsf.org/robots.txt`` disallows
``?format=json``, ``?format=json-pretty``, ``?format=ical``, ``?format=page-context``
and ``?format=main-content`` for *every* agent; ``?format=rss`` is allowed and
sufficient. The fetch layer would block those anyway, but a seed link carrying
one is skipped here before a request is ever built — see
:data:`DISALLOWED_QUERY_FORMATS`. Seeds pointing at another host are skipped for
the same reason in a different direction: "fetch only the URLs in
``sources.yaml``" survives a two-step flow only if step two stays on the site
step one came from.

Where JSON-LD looks available and is not
----------------------------------------

Two live cases, both recorded because each one costs an afternoon:

- **The Crucible.** Its product pages carry ``schema.org/Course`` with **no
  ``startDate``, no ``offers`` and no ``hasCourseInstance``.** A ``Course`` is
  not an ``Event`` and there is nothing to date, so a ``jsonld`` source there
  would return empty forever without ever erroring. That is exactly why
  :attr:`JsonLdProblem.NO_EVENTS` exists and says *what it found instead*:
  "0 events" and "0 events because every object here is an undated Course" must
  not look alike in ``health.json``. The Crucible is registered as ``json``
  (issue 0022) against the WooCommerce Store API, not here.
- **Eventbrite organizer pages no longer emit JSON-LD at all.** Humanmade's was
  registered as ``jsonld`` and would have returned empty forever after Eventbrite
  moved organizer profiles to Next.js; those are ``nextdata`` now (issue 0023).
  Individual Eventbrite *event* pages do still emit JSON-LD, and they emit
  ``"@type": ["Event", "EducationEvent"]`` — a list, and a subtype — which is why
  :func:`is_event_type` accepts both rather than testing ``== "Event"``.

Why ``extruct``, and the two things it does not do
--------------------------------------------------

CLAUDE.md picked ``extruct`` for the JSON-LD edge cases, and all three of the
shapes it names appear in this project's real sources: ``@graph`` nesting, a
bare top-level array, and ``ItemList``/``ListItem`` wrappers. ``extruct`` flattens
a bare array for us. It does **not** flatten ``@graph`` or ``ItemList`` — those
stay nested, so :func:`iter_events` walks them.

The second thing is sharper: **one malformed ld+json block raises and takes the
whole page's extraction with it.** ``extruct.extract()`` over a document with
four good blocks and one truncated one yields nothing at all. So the blocks are
enumerated first with ``extruct``'s own XPath and handed to
``JsonLdExtractor`` one at a time: a bad block is reported as a bad block
(:attr:`JsonLdPage.bad_blocks`) and the good ones still arrive. "Collect every
ld+json block, not just the first" is the requirement; not losing four to a
fifth is the same requirement stated in the other direction.

Dates, offsets, and the one place a name is supplied
-----------------------------------------------------

``startDate``/``endDate`` are parsed with their offsets intact:
``2026-08-08T18:00:00-0700`` keeps ``utcoffset() == -7h`` and stays that exact
instant. A bare ``2026-08-12`` is an all-day date and is passed on as a
:class:`datetime.date`, never coerced to midnight anything — that anchoring is
``normalize.py``'s job and it has a policy for it.

A numeric offset is not a timezone, though, and everything downstream needs a
resolvable IANA name: :func:`pipeline.normalize.day_start_utc` and issue 0016's
health filter both call ``ZoneInfo`` on :attr:`pipeline.normalize.Event.tz`, and
``ZoneInfo("UTC-07:00")`` is an error, not a zone. So timed events carry
:data:`DEFAULT_ZONE` in :attr:`~pipeline.adapters.ics.IcsEvent.tz` as the name to
*render* in, while the instant remains exactly what the source wrote. Every space
in this registry is in the Bay Area and ``defaults.timezone`` in ``sources.yaml``
says so. :attr:`JsonLdEvent.source_offset` keeps the source's own offset and
:attr:`JsonLdEvent.zone_matches_offset` records whether the two agree, so a feed
that starts emitting ``+0100`` is visible rather than quietly re-rendered.

``location`` is left exactly as the source gave it — including empty
-------------------------------------------------------------------

The Box Shop's per-event JSON-LD has an empty ``location``. It is **not** filled
in here. :class:`pipeline.normalize.VenuePolicy` (issue 0009) owns
``address_override`` and applies a deliberate majority-based rule: blank and
URL-only locations take the override, a minority venue keeps its own address and
is flagged ``off_site``. Sudo Room's Luma feed genuinely runs an event at The Box
Shop in San Francisco, and an adapter that helpfully pasted a house address over
an empty field would erase the evidence that policy runs on.

Implemented by issue 0020.
"""

from __future__ import annotations

import datetime as dt
import html as html_module
import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import lxml.etree
from dateutil.parser import isoparse
from extruct.jsonld import JsonLdExtractor
from extruct.utils import parse_html

from pipeline.adapters.ics import DEFAULT_HORIZON_DAYS, IcsEvent, IcsParse
from pipeline.config import SourceRef
from pipeline.fetch import (
    HTML_CONTENT_TYPES,
    XML_CONTENT_TYPES,
    FetchResult,
    Fetcher,
    Outcome,
    request_url,
)

# --------------------------------------------------------------------------- knobs

#: The zone timed events are *rendered* in. Not a guess about the source: the
#: offset the source wrote is preserved on the value itself, and this only
#: supplies the IANA name every downstream ``ZoneInfo`` call needs. It matches
#: ``defaults.timezone`` in ``sources.yaml``.
DEFAULT_ZONE = "America/Los_Angeles"

#: Hard ceiling on per-event follow-up fetches from one seed list. The Box Shop
#: runs 8-12 public events a year and its ``?format=rss`` held **one** item on
#: 2026-08-05, so this is an order of magnitude of headroom. It exists because
#: each follow-up is a separate polite request against a small nonprofit's site,
#: and because a feed that suddenly offers 400 links is a source change that
#: wants a human, not 400 requests.
MAX_SEED_FETCHES = 20

#: Squarespace ``?format=`` values disallowed in ``boxshopsf.org/robots.txt`` for
#: every agent. A seed link carrying one of these is skipped before a request is
#: constructed. ``rss`` is deliberately absent — it is allowed, and sufficient.
DISALLOWED_QUERY_FORMATS: frozenset[str] = frozenset(
    {"json", "json-pretty", "ical", "page-context", "main-content"}
)

#: ``@type`` values treated as an event. schema.org's ``Event`` subtypes mostly
#: end in ``Event`` (``EducationEvent``, ``MusicEvent``, ``SocialEvent`` …) and
#: :func:`is_event_type` uses that suffix as well — but ``Festival``, ``Hackathon``
#: and ``CourseInstance`` do not, so they are named. ``CourseInstance`` is the
#: dated half of a ``Course``: the thing The Crucible's pages conspicuously lack.
EVENT_TYPES: frozenset[str] = frozenset(
    {
        "Event",
        "EventSeries",
        "Festival",
        "Hackathon",
        "CourseInstance",
    }
)

#: Keys whose values are containers of other objects rather than properties of
#: an event. The walk descends through these and stops at anything it recognizes
#: as an event, so an ``Event`` nested under another ``Event``'s ``location`` or
#: ``organizer`` is never counted twice.
CONTAINER_KEYS: tuple[str, ...] = (
    "@graph",
    "itemListElement",
    "item",
    "mainEntity",
    "mainEntityOfPage",
    "hasCourseInstance",
    "subEvent",
    "events",
)

#: How far into a body to look before deciding what kind of document it is.
_SNIFF_BYTES = 4096

#: XPath ``extruct`` itself uses to find ld+json blocks. Reused rather than
#: re-invented so "every block" means the same set of blocks it would have found.
_LD_XPATH = lxml.etree.XPath('descendant-or-self::script[@type="application/ld+json"]')

_UNSAFE_LABEL_RE = re.compile(r"[^A-Za-z0-9._-]+")

#: A bare number, the only ``offers.price`` shape a currency symbol may be
#: attached to. Everything else is left as the sentence it is.
_NUMERIC_PRICE_RE = re.compile(r"\d+(\.\d+)?")


# --------------------------------------------------------------------------- outcomes


class JsonLdProblem(str, Enum):
    """Why a :class:`JsonLdParse` is not usable. ``NONE`` means it is.

    The first five mirror :class:`~pipeline.adapters.ics.IcsProblem` so the
    CLI's ``record.problem`` vocabulary stays one vocabulary. The rest are the
    failures only an embedded-metadata adapter can have, and they are kept apart
    because "0 events" from each of them means something different to a human
    reading ``health.json`` at 09:00.
    """

    NONE = "none"
    TRANSPORT = "transport"
    NOT_MODIFIED = "not_modified"
    EMPTY_BODY = "empty_body"
    WRONG_CONTENT_TYPE = "wrong_content_type"

    #: Non-2xx on the registered URL.
    HTTP_ERROR = "http_error"
    #: The document parsed but carries **no** ``application/ld+json`` block at
    #: all. A page that used to have one and now does not is a drifted source —
    #: Eventbrite's organizer pages are the recorded case.
    NO_JSONLD = "no_jsonld"
    #: ld+json is present and contains no ``Event`` of any type. The Crucible's
    #: ``Course``-without-``hasCourseInstance`` case, said out loud.
    NO_EVENTS = "no_events"
    #: A seed feed was fetched and every single follow-up page failed. One 404
    #: among several is a note; all of them is a source that is down.
    SEED_FETCH_FAILED = "seed_fetch_failed"
    #: The body could not be parsed as a document at all.
    UNPARSEABLE = "unparseable"


# --------------------------------------------------------------------------- events


@dataclass(frozen=True)
class JsonLdEvent(IcsEvent):
    """One ``schema.org/Event``, in the intermediate shape ``ics`` produces.

    A subclass rather than a parallel class, for the same reason
    :class:`~pipeline.adapters.tribe_rest.TribeEvent` is one: ``normalize.py``
    consumes every adapter through :func:`~pipeline.normalize.from_ics_event`,
    and the moment a second shape appears one of them starts quietly losing
    fields. Everything JSON-LD adds is appended below.
    """

    #: The ``@type`` as written, joined when it was a list:
    #: ``"Event"``, ``"EducationEvent"``, ``"Event+EducationEvent"``.
    ld_type: str = "Event"

    #: The offset the source wrote, as ``"-07:00"`` / ``"+00:00"``, or ``None``
    #: for an all-day date or a floating time. Provenance for
    #: :attr:`~pipeline.adapters.ics.IcsEvent.tz`, which carries an IANA name.
    source_offset: str | None = None

    #: False when the source's offset disagrees with :data:`DEFAULT_ZONE` at that
    #: instant. The instant is still exact; only the rendering zone is a
    #: statement, and a feed that starts emitting ``+0100`` shows up here.
    zone_matches_offset: bool = True

    #: The page this event was read from. For the one-page flow that is the
    #: registered URL; for the seed flow it is the ``/events/<slug>`` page, which
    #: is what makes a per-event failure attributable.
    page_url: str = ""

    #: ``offers.price`` rendered as source text, never parsed to a number — the
    #: same rule :class:`~pipeline.adapters.tribe_rest.TribeEvent` follows.
    cost: str | None = None

    @property
    def price(self) -> str | None:
        """Alias :attr:`cost` under the canonical schema's name."""
        return self.cost


# --------------------------------------------------------------------------- pages


@dataclass(frozen=True)
class JsonLdPage:
    """One document that was read for JSON-LD. The seed feed gets one of these
    too, so ``pages`` is a complete account of what was fetched."""

    url: str
    #: ``<script type="application/ld+json">`` elements found.
    block_count: int = 0
    #: Blocks that were not valid JSON. Counted, never fatal on their own.
    bad_blocks: int = 0
    #: Top-level ld+json items decoded from the good blocks.
    item_count: int = 0
    #: ``Event`` objects found, **before** horizon clipping.
    event_count: int = 0
    #: ``@type`` values seen anywhere in this page's ld+json. What makes
    #: "a Course, not an Event" reportable rather than invisible.
    types: tuple[str, ...] = ()
    problem: JsonLdProblem = JsonLdProblem.NONE
    note: str | None = None
    #: True for the RSS/Atom seed document rather than an HTML page.
    is_seed: bool = False

    @property
    def ok(self) -> bool:
        return self.problem is JsonLdProblem.NONE


# --------------------------------------------------------------------------- result


@dataclass(frozen=True)
class JsonLdParse(IcsParse):
    """The adapter's return value. Same surface as :class:`IcsParse`, plus pages.

    Subclasses :class:`~pipeline.adapters.ics.IcsParse` so every consumer written
    against the ICS adapter — ``normalize_ics``, the CLI's ``SourceRecord``,
    issue 0016's gates — works unchanged.
    """

    problem: JsonLdProblem = JsonLdProblem.NONE  # type: ignore[assignment]

    #: One entry per document read, seed feed first when there was one.
    pages: tuple[JsonLdPage, ...] = ()
    #: Every item ``<link>`` the seed feed offered, in feed order, before the
    #: cap and before any skipping. Empty for the one-page flow.
    seed_urls: tuple[str, ...] = ()
    #: Seed URLs actually fetched.
    followed: tuple[str, ...] = ()
    #: Seed URLs deliberately not fetched, with the reason: over the cap, off
    #: host, or a robots-disallowed ``?format=``.
    skipped_seeds: tuple[tuple[str, str], ...] = ()
    #: True when a seed list was read but not followed — the cap was hit, or no
    #: fetcher was supplied. Never silently: it is also in ``error``.
    truncated: bool = False

    @property
    def ok(self) -> bool:
        """True when the document was what it claimed and we read it.

        Zero events with ``ok=True`` is legitimate here and routine for The Box
        Shop, which runs 8-12 events a year and carries ``allow_zero``. Zero
        events because the page holds nothing but an undated ``Course`` is
        :attr:`JsonLdProblem.NO_EVENTS` and is **not** ok.
        """
        return self.problem is JsonLdProblem.NONE

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def block_count(self) -> int:
        """ld+json blocks collected across every page read."""
        return sum(page.block_count for page in self.pages)

    @property
    def bad_block_count(self) -> int:
        return sum(page.bad_blocks for page in self.pages)

    @property
    def seed_count(self) -> int:
        return len(self.seed_urls)

    @property
    def types_seen(self) -> tuple[str, ...]:
        """Every ``@type`` seen, sorted. The Crucible's ``Course`` shows up here."""
        seen: set[str] = set()
        for page in self.pages:
            seen.update(page.types)
        return tuple(sorted(seen))

    @property
    def failed_pages(self) -> tuple[JsonLdPage, ...]:
        return tuple(page for page in self.pages if not page.ok)

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        head = f"{self.space_id}:{self.label}" if self.space_id or self.label else "<jsonld>"
        if not self.ok:
            return f"{head} FAILED {self.problem.value}: {self.error}"
        return (
            f"{head} {self.event_count} events in {self.horizon_days}d "
            f"(from {self.vevent_count} across {self.page_count} pages, "
            f"{self.block_count} ld+json blocks)"
        )


# --------------------------------------------------------------------------- text


def _clean(value: Any) -> str | None:
    """Trim and HTML-unescape one text field, or ``None`` if it is empty.

    Script contents are *raw text* in HTML, so an ``&amp;`` written into a
    JSON-LD string arrives here verbatim rather than decoded by the parser.
    Ace's titles carry them; a calendar full of ``&amp;`` is the kind of thing
    nobody files a bug for.
    """
    if value is None or isinstance(value, (dict, list)):
        return None
    if isinstance(value, bool):
        return None
    text = html_module.unescape(str(value)).strip()
    return text or None


def _strings(value: Any) -> tuple[str, ...]:
    """``"a"`` / ``["a", "b"]`` / ``[{"name": "a"}]`` → ``("a", "b")``."""
    if value is None:
        return ()
    entries: Iterable[Any] = value if isinstance(value, (list, tuple)) else [value]
    out: list[str] = []
    for entry in entries:
        text = _clean(entry.get("name")) if isinstance(entry, dict) else _clean(entry)
        if text and text not in out:
            out.append(text)
    return tuple(out)


def _type_names(node: Any) -> tuple[str, ...]:
    """``@type`` as a tuple, whether it was a string or a list.

    Eventbrite event pages emit ``["Event", "EducationEvent"]``. Testing
    ``node.get("@type") == "Event"`` misses every one of them.
    """
    if not isinstance(node, dict):
        return ()
    return _strings(node.get("@type") or node.get("type"))


def is_event_type(node: Any) -> bool:
    """True when this object is a ``schema.org/Event`` of some flavour.

    Accepts a ``@type`` list and accepts subtypes: ``EducationEvent`` (what
    Eventbrite event pages emit), ``MusicEvent``, ``SocialEvent`` and the rest
    of the ``*Event`` family, plus the few named in :data:`EVENT_TYPES` that do
    not carry the suffix.
    """
    for name in _type_names(node):
        bare = name.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
        if bare in EVENT_TYPES or (bare.endswith("Event") and bare != "Event"):
            return True
        if bare == "Event":
            return True
    return False


# --------------------------------------------------------------------------- blocks


def ld_blocks(document: str) -> tuple[str, ...]:
    """Every ``<script type="application/ld+json">`` body on the page, in order.

    **Every one**, not the first: Ace's ``/calendar/`` has a ``WebSite`` block
    alongside the events, and a page that carries its events in the third block
    is not unusual. Uses ``extruct``'s own XPath so this is the same set of
    blocks ``extruct.extract()`` would have seen.
    """
    tree = parse_html(document, "UTF-8")
    return tuple(str(node.xpath("string()")) for node in _LD_XPATH(tree))


def ld_items(document: str) -> tuple[list[Any], int, int]:
    """``(items, block_count, bad_blocks)`` — decoded one block at a time.

    Per-block rather than one ``extruct.extract()`` call over the document,
    because **one malformed block raises and loses the whole page**. A truncated
    fifth block must not cost four good ones; the count of bad blocks is
    reported instead.
    """
    blocks = ld_blocks(document)
    extractor = JsonLdExtractor()
    items: list[Any] = []
    bad = 0
    for block in blocks:
        if not block.strip():
            bad += 1
            continue
        try:
            # Re-wrapped rather than reaching into extruct's private decoder.
            # Script bodies are raw text in HTML, so nothing was unescaped on
            # the way out and nothing is re-escaped on the way back in.
            decoded = extractor.extract(
                f'<script type="application/ld+json">{block}</script>'
            )
        except Exception:
            # Includes JSONDecodeError, which extruct raises rather than skips.
            bad += 1
            continue
        items.extend(decoded)
    return items, len(blocks), bad


def iter_events(items: Iterable[Any]) -> Iterator[dict[str, Any]]:
    """Walk decoded ld+json for ``Event`` objects, in document order.

    Handles the four shapes that appear in this project's real sources:

    - a **bare array** of ``Event`` (Ace) — ``extruct`` flattens this one for us
    - **``@graph``** nesting, which it does not flatten
    - **``ItemList`` / ``ListItem``** wrappers, which it also does not
    - a plain single ``Event`` on a detail page (The Box Shop)

    The walk stops descending at an event, so an ``Event`` referenced from
    another event's ``location``, ``organizer`` or ``subEvent`` is not counted
    twice. Cycles are impossible in decoded JSON, but the same object can be
    reached twice through ``@graph``, so identity is tracked.
    """
    seen: set[int] = set()

    def walk(node: Any) -> Iterator[dict[str, Any]]:
        if isinstance(node, list):
            for entry in node:
                yield from walk(entry)
            return
        if not isinstance(node, dict):
            return
        if id(node) in seen:
            return
        seen.add(id(node))
        if is_event_type(node):
            yield node
            return
        for key in CONTAINER_KEYS:
            if key in node:
                yield from walk(node[key])

    for item in items:
        yield from walk(item)


def collect_types(items: Iterable[Any], limit: int = 40) -> tuple[str, ...]:
    """Every ``@type`` anywhere in the decoded ld+json, for diagnostics.

    This is what turns "0 events" into "0 events, and what is here is
    ``Course`` and ``Product``" — the difference between a report and a shrug.
    """
    found: list[str] = []

    def walk(node: Any) -> None:
        if len(found) >= limit:
            return
        if isinstance(node, list):
            for entry in node:
                walk(entry)
            return
        if not isinstance(node, dict):
            return
        for name in _type_names(node):
            if name not in found and len(found) < limit:
                found.append(name)
        for value in node.values():
            walk(value)

    for item in items:
        walk(item)
    return tuple(sorted(found))


# --------------------------------------------------------------------------- dates


def _zone() -> ZoneInfo | None:
    try:
        return ZoneInfo(DEFAULT_ZONE)
    except (ZoneInfoNotFoundError, ValueError, KeyError):  # pragma: no cover
        return None


def _offset_text(value: dt.datetime) -> str | None:
    offset = value.utcoffset()
    if offset is None:
        return None
    total = int(offset.total_seconds())
    sign = "-" if total < 0 else "+"
    total = abs(total)
    return f"{sign}{total // 3600:02d}:{(total % 3600) // 60:02d}"


@dataclass(frozen=True)
class _Moment:
    """One parsed ``startDate``/``endDate``, with how it was written."""

    value: dt.date | dt.datetime | None = None
    #: ``"date"`` | ``"utc"`` | ``"offset"`` | ``"floating"``.
    form: str = "unknown"
    offset: str | None = None
    matches_zone: bool = True


def parse_ld_datetime(value: Any) -> _Moment:
    """Parse a schema.org date-time, **keeping the offset it was written with**.

    ``2026-08-08T18:00:00-0700`` and ``2026-08-05T18:00:00-07:00`` are the same
    thing and both appear; ``dateutil`` takes either. The result keeps that
    exact offset — no conversion to UTC and no conversion to a named zone, so
    the value that reaches ``normalize.py`` is the instant the source stated.

    A bare ``2026-08-12`` is a :class:`datetime.date`: all-day, unanchored.
    Anchoring it to midnight-somewhere is ``normalize.py``'s decision and it has
    a documented policy for it.

    Never raises. An unparseable value yields an empty :class:`_Moment` and the
    event is dropped with the rest of the undated ones.
    """
    text = _clean(value)
    if not text:
        return _Moment()

    # A date with no time component. Squarespace and TEC both emit these for
    # all-day entries, and "2026-08-12T00:00" is *not* the same statement.
    if "T" not in text and " " not in text and len(text) <= 10:
        try:
            return _Moment(value=dt.date.fromisoformat(text), form="date")
        except ValueError:
            return _Moment()

    try:
        parsed = isoparse(text)
    except (ValueError, OverflowError, TypeError):
        return _Moment()

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return _Moment(value=parsed.replace(tzinfo=None), form="floating")

    # dateutil hands back tzutc()/tzoffset(); a plain datetime.timezone renders
    # readably and compares equal on the offset, which is all that matters.
    offset = parsed.utcoffset() or dt.timedelta(0)
    aware = parsed.replace(tzinfo=dt.timezone(offset))
    zone = _zone()
    matches = True
    if zone is not None:
        matches = aware.astimezone(zone).utcoffset() == offset
    form = "utc" if offset == dt.timedelta(0) and text.strip().endswith(("Z", "z")) else "offset"
    return _Moment(value=aware, form=form, offset=_offset_text(aware), matches_zone=matches)


# --------------------------------------------------------------------------- fields


def _location(node: Any) -> str | None:
    """The single ``LOCATION``-shaped string ``normalize.py`` consumes.

    **Empty stays empty.** The Box Shop's per-event JSON-LD carries
    ``"location": {}`` and filling it in from the space record here would take
    the decision away from :class:`pipeline.normalize.VenuePolicy`, which owns
    ``address_override`` and applies a majority rule precisely so a genuinely
    off-site event does not get relocated to the house address.
    """
    if isinstance(node, list):
        for entry in node:
            resolved = _location(entry)
            if resolved:
                return resolved
        return None
    if isinstance(node, str):
        return _clean(node)
    if not isinstance(node, dict):
        return None

    name = _clean(node.get("name"))
    address = node.get("address")
    if isinstance(address, str):
        address_text = _clean(address)
    elif isinstance(address, dict):
        parts = [
            _clean(address.get(key))
            for key in (
                "streetAddress",
                "addressLocality",
                "addressRegion",
                "postalCode",
                "addressCountry",
            )
        ]
        seen: list[str] = []
        for part in parts:
            if part and part not in seen:
                seen.append(part)
        address_text = ", ".join(seen) or None
    else:
        address_text = None

    pieces = [piece for piece in (name, address_text) if piece]
    return ", ".join(pieces) or None


def _price(node: Any) -> str | None:
    """``offers.price`` as **source text**, never parsed to a number.

    Same rule as ``tribe_rest``'s ``cost``: "sliding scale $10-30" and "free for
    members" are real values in this dataset, and a float turns the first into a
    wrong number and the second into ``None``. A ``$`` is prefixed only when the
    value is a bare USD number — decorating "sliding scale $10-30" into
    "$sliding scale $10-30" is the same class of damage in miniature.
    """
    if isinstance(node, list):
        for entry in node:
            price = _price(entry)
            if price:
                return price
        return None
    if not isinstance(node, dict):
        return _clean(node)

    price = _clean(node.get("price"))
    if price is None:
        return None
    currency = (_clean(node.get("priceCurrency")) or "").upper()
    numeric = _NUMERIC_PRICE_RE.fullmatch(price) is not None
    if not numeric:
        return price
    if currency in ("", "USD"):
        return f"${price}"
    return f"{price} {currency}"


def _status(value: Any) -> str | None:
    """``eventStatus`` in ICS ``STATUS`` vocabulary, or ``None``."""
    text = _clean(value)
    if text is None:
        return None
    bare = text.rsplit("/", 1)[-1].lower()
    if bare in ("eventcancelled", "eventpostponed"):
        return "CANCELLED"
    if bare == "eventscheduled":
        return "CONFIRMED"
    return text.upper()


def _build_event(node: dict[str, Any], *, page_url: str) -> JsonLdEvent | None:
    """One ``schema.org/Event`` → one :class:`JsonLdEvent`, or ``None`` if undated.

    An event with no usable ``startDate`` is dropped here rather than handed on
    with ``start=None``: :func:`pipeline.normalize.normalize_event` would drop it
    as ``NO_START`` anyway, and a half-built event in the intermediate shape is a
    trap for the next reader. **The Crucible's ``Course`` objects are exactly
    this shape** — which is why the count of what was seen is reported by the
    caller instead of silently becoming zero.
    """
    start = parse_ld_datetime(node.get("startDate"))
    if start.value is None:
        return None
    end = parse_ld_datetime(node.get("endDate"))

    all_day = start.form == "date"
    end_value: dt.date | dt.datetime | None = end.value
    if all_day and isinstance(end_value, dt.datetime):
        # A dated start with a timed end is a source contradiction. Keep the
        # day, drop the time: normalize.py raises if an all-day event carries a
        # datetime, and it is right to.
        end_value = end_value.date()
    if not all_day and isinstance(end_value, dt.date) and not isinstance(
        end_value, dt.datetime
    ):
        end_value = None

    days = 1
    multi_day = False
    if isinstance(start.value, dt.datetime) and isinstance(end_value, dt.datetime):
        days = max((end_value.date() - start.value.date()).days + 1, 1)
        multi_day = days > 1
    elif isinstance(start.value, dt.date) and isinstance(end_value, dt.date):
        if end_value < start.value:
            end_value = start.value
        days = max((end_value - start.value).days + 1, 1)
        multi_day = days > 1
    elif isinstance(start.value, dt.date) and not isinstance(start.value, dt.datetime):
        end_value = start.value

    url = _clean(node.get("url")) or _clean(node.get("@id")) or (page_url or None)
    # schema.org has no UID. `@id` is the stable identity when a source sets
    # one, and the canonical event URL is the next best thing — both are stable
    # across runs, which is the whole requirement. When neither exists,
    # normalize.py falls back to sha1(space + start + title).
    uid = _clean(node.get("@id")) or _clean(node.get("url"))

    image = node.get("image")
    if isinstance(image, list):
        image = image[0] if image else None
    if isinstance(image, dict):
        image = image.get("url")

    return JsonLdEvent(
        uid=uid,
        title=_clean(node.get("name")) or _clean(node.get("headline")),
        start=start.value,
        end=end_value,
        all_day=all_day,
        multi_day=multi_day,
        days=days,
        # A numeric offset is not a zone, and everything downstream calls
        # ZoneInfo on this. See the module docstring: the instant is the
        # source's, the name is ours, and source_offset keeps the evidence.
        tz=None if all_day else DEFAULT_ZONE,
        source_tz=None,
        dtstart_form=start.form,
        location=_location(node.get("location")),
        description=_clean(node.get("description")),
        url=url,
        categories=_strings(node.get("keywords")),
        status=_status(node.get("eventStatus")),
        organizer=(_strings(node.get("organizer")) or (None,))[0],
        # schema.org has no recurrence rule; ``eventSchedule`` exists but no
        # source in this registry emits one, so nothing is expanded here.
        recurring=False,
        last_modified=None,
        dtstamp=None,
        ld_type="+".join(_type_names(node)) or "Event",
        source_offset=start.offset,
        zone_matches_offset=start.matches_zone,
        page_url=page_url,
        cost=_price(node.get("offers")),
    )


def _sort_key(event: JsonLdEvent) -> tuple[dt.date, int, dt.time, str]:
    """Same ordering rule as the ICS adapter: day, all-day first, then time."""
    start = event.start
    if isinstance(start, dt.datetime):
        moment = start.astimezone(dt.timezone.utc) if start.tzinfo else start
        return (moment.date(), 1, moment.time(), event.uid or "")
    return (start, 0, dt.time.min, event.uid or "")


# --------------------------------------------------------------------------- seeds


def looks_like_feed(text: str) -> bool:
    """True when the body opens as an RSS or Atom document rather than a page."""
    head = text[:_SNIFF_BYTES].lstrip("﻿ \t\r\n").lower()
    if head.startswith("<?xml"):
        return "<rss" in head or "<feed" in head or "<rdf" in head
    return head.startswith(("<rss", "<feed", "<rdf"))


def looks_like_html(text: str) -> bool:
    head = text[:_SNIFF_BYTES].lstrip("﻿ \t\r\n").lower()
    return head.startswith(("<!doctype html", "<html", "<head", "<body", "<!--"))


def _localname(tag: Any) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def seed_links(document: str, *, base_url: str = "") -> tuple[str, ...]:
    """Item ``<link>`` URLs from an RSS or Atom feed, in feed order.

    Namespace-agnostic on purpose: Squarespace's feed carries ``content:`` and
    ``media:`` namespaces, other producers carry others, and matching on the
    local name is what keeps this from being a per-vendor special case. Atom's
    ``<link href="…" rel="alternate">`` and RSS's ``<link>text</link>`` are both
    read.

    **This is not a crawl.** It reads the links a registered feed offers and
    nothing else — no ``<a href>``, no sitemap, no following anything a page
    happens to mention.
    """
    try:
        parser = lxml.etree.XMLParser(recover=True, resolve_entities=False, no_network=True)
        tree = lxml.etree.fromstring(document.encode("utf-8"), parser=parser)
    except lxml.etree.XMLSyntaxError:  # pragma: no cover - recover=True rarely fails
        return ()
    if tree is None:
        return ()

    links: list[str] = []
    for element in tree.iter():
        if _localname(element.tag) not in ("item", "entry"):
            continue
        for child in element.iter():
            if _localname(child.tag) != "link":
                continue
            rel = (child.get("rel") or "alternate").lower()
            href = child.get("href") or (child.text or "")
            if rel != "alternate":
                continue
            href = href.strip()
            if not href:
                continue
            resolved = urljoin(base_url, href) if base_url else href
            if resolved not in links:
                links.append(resolved)
            break
    return tuple(links)


def is_disallowed_route(url: str | httpx.URL) -> bool:
    """True for a ``?format=`` value ``boxshopsf.org/robots.txt`` disallows.

    Checked before a request is built, not after it is refused. "A disallow
    means we do not fetch it — not that we find an equivalent route", and the
    cheapest way to honor that is never to construct the URL.
    """
    try:
        params = httpx.URL(str(url)).params
    except (ValueError, httpx.InvalidURL):  # pragma: no cover - defensive
        return False
    return any(
        str(value).strip().lower() in DISALLOWED_QUERY_FORMATS
        for value in params.get_list("format")
    )


def _label_suffix(url: str) -> str:
    """A short, filesystem-safe name for one follow-up page's ``raw/`` entry."""
    path = httpx.URL(url).path.rstrip("/")
    slug = path.rsplit("/", 1)[-1] or "page"
    return _UNSAFE_LABEL_RE.sub("-", slug).strip("-")[:48] or "page"


# --------------------------------------------------------------------------- decoding


@dataclass(frozen=True)
class _PageResult:
    """Internal: one page's decode — its record, its events, and its verdict."""

    page: JsonLdPage
    events: tuple[JsonLdEvent, ...] = ()


def _transport_problem(
    result: FetchResult, *, where: str
) -> tuple[JsonLdProblem, str] | None:
    """Everything that can be wrong before the body is worth looking at."""
    if result.outcome is Outcome.NOT_MODIFIED:
        return (
            JsonLdProblem.NOT_MODIFIED,
            f"{where}: HTTP 304, unchanged since the last run — reuse the stored "
            "events rather than treating this as zero",
        )
    if result.outcome is not Outcome.FETCHED:
        detail = f" ({result.reason})" if result.reason else ""
        return (JsonLdProblem.TRANSPORT, f"{where}: {result.outcome.value}{detail}")

    status = result.status_code
    if status is not None and not 200 <= status < 300:
        return (JsonLdProblem.HTTP_ERROR, f"{where}: HTTP {status}")
    if not result.has_body:
        return (JsonLdProblem.EMPTY_BODY, f"{where}: HTTP {status} with an empty body")
    return None


def _decode_page(
    text: str,
    *,
    url: str,
    where: str,
    content_type: str | None = None,
    content_type_is_html: bool = True,
) -> _PageResult:
    """Read one HTML document for ``schema.org/Event``. Never raises.

    Every judgement CLAUDE.md asks for happens here, in order: content type,
    then whether there is any ld+json at all, then whether any of it is an
    event. The last two are separate problems because they have different
    causes and different fixes — a page that lost its ld+json has drifted
    (Eventbrite), a page full of undated ``Course`` objects never had events to
    begin with (The Crucible).

    ``content_type_is_html`` is the caller's verdict from
    :meth:`~pipeline.fetch.FetchResult.content_type_is`; the header itself is
    passed only so the message can quote it.
    """
    tolerated: str | None = None
    items, blocks, bad = ld_items(text)
    types = collect_types(items)

    if not content_type_is_html:
        # A declared type that is not HTML is tolerated only when the body is
        # visibly an HTML page carrying ld+json. Some hosts serve pages as
        # text/plain; that is sloppy, not dishonest. A text/calendar body with
        # no ld+json in it is a different source entirely.
        if blocks and looks_like_html(text):
            tolerated = content_type
        else:
            return _PageResult(
                page=JsonLdPage(
                    url=url,
                    block_count=blocks,
                    bad_blocks=bad,
                    item_count=len(items),
                    types=types,
                    problem=JsonLdProblem.WRONG_CONTENT_TYPE,
                    note=(
                        f"{where}: expected {' or '.join(HTML_CONTENT_TYPES)}, got "
                        f"{content_type or '<no Content-Type header>'} and found {blocks} "
                        "ld+json block(s) — HTTP 200 is not success, and a body that is "
                        "not the page we registered must not be read as a calendar with "
                        "nothing on it"
                    ),
                )
            )

    if blocks == 0:
        return _PageResult(
            page=JsonLdPage(
                url=url,
                block_count=0,
                bad_blocks=bad,
                item_count=0,
                problem=JsonLdProblem.NO_JSONLD,
                note=(
                    f"{where}: no <script type=\"application/ld+json\"> block on the page. "
                    "This is a drifted source, not an empty calendar — Eventbrite's "
                    "organizer pages are the recorded case: they stopped emitting JSON-LD "
                    "when the profiles moved to Next.js, and a jsonld source there returned "
                    "empty forever without erroring (see issue 0023, adapter nextdata)"
                ),
            )
        )

    events: list[JsonLdEvent] = []
    found = 0
    for node in iter_events(items):
        found += 1
        event = _build_event(node, page_url=url)
        if event is not None:
            events.append(event)

    note = None
    problem = JsonLdProblem.NONE
    if found == 0:
        problem = JsonLdProblem.NO_EVENTS
        note = (
            f"{where}: {blocks} ld+json block(s), {len(items)} item(s), and no "
            f"schema.org/Event of any type. Types present: "
            f"{', '.join(types) or '<none>'}. The Crucible is the recorded case — its "
            "product pages carry schema.org/Course with no startDate, no offers and no "
            "hasCourseInstance, so a jsonld source there returns empty forever without "
            "ever erroring. Reported rather than returned as zero events"
        )
    elif found and not events:
        problem = JsonLdProblem.NO_EVENTS
        note = (
            f"{where}: {found} schema.org/Event object(s), none of them carrying a "
            "usable startDate. An Event with no date is not an event this calendar can "
            "publish, and it must not be reported as an empty page"
        )
    elif tolerated:
        note = f"content type {tolerated!r} tolerated: the body is an HTML page with ld+json"

    if bad and note is None:
        note = (
            f"{bad} of {blocks} ld+json block(s) were not valid JSON and were skipped; "
            "the rest were read"
        )

    return _PageResult(
        page=JsonLdPage(
            url=url,
            block_count=blocks,
            bad_blocks=bad,
            item_count=len(items),
            event_count=found,
            types=types,
            problem=problem,
            note=note,
        ),
        events=tuple(events),
    )


# --------------------------------------------------------------------------- building


def _build(
    pages: Sequence[JsonLdPage],
    events: Sequence[JsonLdEvent],
    *,
    space_id: str,
    label: str,
    source_url: str,
    horizon_days: int,
    window_start: dt.date,
    window_end: dt.date,
    now: dt.datetime,
    problem: JsonLdProblem = JsonLdProblem.NONE,
    error: str | None = None,
    seed_urls: Sequence[str] = (),
    followed: Sequence[str] = (),
    skipped_seeds: Sequence[tuple[str, str]] = (),
    truncated: bool = False,
) -> JsonLdParse:
    """Assemble decoded pages into the returned :class:`JsonLdParse`.

    Horizon clipping happens here and on the event's own local day, so the count
    this reports is the post-clip one issue 0016's gates read.
    """
    kept: list[JsonLdEvent] = []
    for event in events:
        start = event.start
        day = start.date() if isinstance(start, dt.datetime) else start
        if window_start <= day <= window_end:
            kept.append(event)

    return JsonLdParse(
        events=tuple(sorted(kept, key=_sort_key)),
        problem=problem,
        error=error,
        space_id=space_id,
        label=label,
        source_url=source_url,
        vevent_count=len(events),
        recurring_vevent_count=0,
        horizon_days=horizon_days,
        window_start=window_start,
        window_end=window_end,
        parsed_at=now,
        pages=tuple(pages),
        seed_urls=tuple(seed_urls),
        followed=tuple(followed),
        skipped_seeds=tuple(skipped_seeds),
        truncated=truncated,
    )


def _failure(
    problem: JsonLdProblem,
    error: str,
    *,
    space_id: str = "",
    label: str = "",
    source_url: str = "",
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    window_start: dt.date | None = None,
    window_end: dt.date | None = None,
    now: dt.datetime | None = None,
    pages: Sequence[JsonLdPage] = (),
) -> JsonLdParse:
    """A reported failure. Never raised — see the ``ics`` module docstring."""
    return JsonLdParse(
        problem=problem,
        error=error,
        space_id=space_id,
        label=label,
        source_url=source_url,
        horizon_days=horizon_days,
        window_start=window_start,
        window_end=window_end,
        parsed_at=now,
        pages=tuple(pages),
    )


# --------------------------------------------------------------------------- parsing


def parse_jsonld(
    result: FetchResult,
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    today: dt.date | None = None,
    now: dt.datetime | None = None,
    strict_content_type: bool = True,
    fetcher: Fetcher | None = None,
    ref: SourceRef | None = None,
    max_seeds: int = MAX_SEED_FETCHES,
) -> JsonLdParse:
    """Parse a fetched document for ``schema.org/Event``. Never raises.

    ``result`` is whatever ``sources.yaml`` pointed at, already fetched by the
    caller. Two flows, chosen by what actually came back rather than by
    configuration:

    **A page** (Ace's ``/calendar/``) is read directly for ld+json.

    **A seed feed** (The Box Shop's ``/events?format=rss``) is read for item
    ``<link>`` URLs, and each one is then fetched through ``fetcher`` — the same
    seam ``tribe_rest`` uses for pagination, so every follow-up shares one
    ``robots.txt`` decision, one rate limiter and one ``raw/`` archive. Called
    **without** a fetcher — a test, or a human reading a file out of ``raw/`` —
    it reports the seed list it found and sets :attr:`JsonLdParse.truncated`
    rather than pretending the source has no events.

    A 404 on one seed page is a note, not the end of the source: these are
    independent events, not pages of one list, so losing one is not the "half a
    calendar that looks healthy" case ``tribe_rest`` refuses to publish. All of
    them failing **is** reported, as :attr:`JsonLdProblem.SEED_FETCH_FAILED`.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    today = today or now.date()
    window_start = today
    window_end = today + dt.timedelta(days=horizon_days)
    where = f"{result.source_key} ({result.url})"

    def fail(problem: JsonLdProblem, error: str, pages: Sequence[JsonLdPage] = ()) -> JsonLdParse:
        return _failure(
            problem,
            error,
            space_id=result.space_id,
            label=result.label,
            source_url=result.url,
            horizon_days=horizon_days,
            window_start=window_start,
            window_end=window_end,
            now=now,
            pages=pages,
        )

    transport = _transport_problem(result, where=where)
    if transport is not None:
        return fail(*transport)

    text = result.text
    seed_mode = result.content_type_is(*XML_CONTENT_TYPES) or looks_like_feed(text)

    # --- the one-page flow ---------------------------------------------------
    if not seed_mode:
        decoded = _decode_page(
            text,
            url=result.final_url or result.url,
            where=where,
            content_type=result.content_type,
            content_type_is_html=(
                not strict_content_type
                or result.content_type is None
                or result.content_type_is(*HTML_CONTENT_TYPES)
            ),
        )
        if not decoded.page.ok:
            return fail(decoded.page.problem, decoded.page.note or "page unreadable", [decoded.page])
        return _build(
            [decoded.page],
            decoded.events,
            space_id=result.space_id,
            label=result.label,
            source_url=result.url,
            horizon_days=horizon_days,
            window_start=window_start,
            window_end=window_end,
            now=now,
            error=decoded.page.note,
        )

    # --- the two-step flow ---------------------------------------------------
    base = result.final_url or result.url
    links = seed_links(text, base_url=base)
    seed_host = httpx.URL(base).host.lower()

    accepted: list[str] = []
    skipped: list[tuple[str, str]] = []
    for link in links:
        if len(accepted) >= max_seeds:
            skipped.append((link, f"over the {max_seeds}-fetch cap"))
            continue
        if is_disallowed_route(link):
            # Never constructed, never requested. robots.txt disallows
            # ?format=json and ?format=ical for everyone on this host.
            skipped.append((link, "robots.txt disallows this ?format= route"))
            continue
        if httpx.URL(link).host.lower() != seed_host:
            # No crawling, no link-following off the registered site.
            skipped.append((link, f"off host ({httpx.URL(link).host})"))
            continue
        accepted.append(link)

    seed_page = JsonLdPage(
        url=base,
        is_seed=True,
        item_count=len(links),
        note=(
            f"seed feed: {len(links)} item link(s); the RSS itself carries no event "
            "date (pubDate is the post date), so every date below comes from the "
            "per-event page"
        ),
    )

    if not links:
        # An events collection with nothing upcoming. The Box Shop runs 8-12
        # events a year and carries allow_zero for exactly this: legitimately
        # empty is not a failure, and calling it one would alert monthly.
        return _build(
            [seed_page],
            (),
            space_id=result.space_id,
            label=result.label,
            source_url=result.url,
            horizon_days=horizon_days,
            window_start=window_start,
            window_end=window_end,
            now=now,
            error=(
                f"{where}: the seed feed carried no item links. For a low-volume space "
                "this is legitimately empty (allow_zero), not a parse failure"
            ),
        )

    if fetcher is None or ref is None:
        # A deliberate single-document call. Say so loudly rather than
        # returning zero events as though the space had none.
        return _build(
            [seed_page],
            (),
            space_id=result.space_id,
            label=result.label,
            source_url=result.url,
            horizon_days=horizon_days,
            window_start=window_start,
            window_end=window_end,
            now=now,
            seed_urls=links,
            skipped_seeds=skipped,
            truncated=True,
            error=(
                f"{where}: {len(links)} seed link(s) found and no fetcher was supplied, "
                "so no per-event page was read. The seed feed carries no event dates of "
                "its own — this is a seed list, not a calendar"
            ),
        )

    pages: list[JsonLdPage] = [seed_page]
    events: list[JsonLdEvent] = []
    followed: list[str] = []
    failures = 0

    for link in accepted:
        followed.append(link)
        page_result = fetcher.fetch_url(ref, link, label_suffix=_label_suffix(link))
        page_where = f"{result.source_key} seed page ({link})"
        transport = _transport_problem(page_result, where=page_where)
        if transport is not None:
            failures += 1
            pages.append(
                JsonLdPage(url=link, problem=transport[0], note=transport[1])
            )
            continue
        decoded = _decode_page(
            page_result.text,
            url=page_result.final_url or link,
            where=page_where,
            content_type=page_result.content_type,
            content_type_is_html=(
                not strict_content_type
                or page_result.content_type is None
                or page_result.content_type_is(*HTML_CONTENT_TYPES)
            ),
        )
        pages.append(decoded.page)
        if not decoded.page.ok:
            failures += 1
            continue
        events.extend(decoded.events)

    notes: list[str] = []
    if skipped:
        notes.append(
            "; ".join(f"skipped {url} ({why})" for url, why in skipped[:5])
            + ("…" if len(skipped) > 5 else "")
        )
    if failures:
        notes.append(
            f"{failures} of {len(followed)} per-event page(s) failed and were skipped: "
            + "; ".join(
                f"{page.url} ({page.problem.value})" for page in pages if not page.ok
            )
        )

    if failures and failures == len(followed):
        # Every follow-up failed. That is not a space with no events, it is a
        # site that is down, and carry-forward (issue 0014) should republish
        # last night's set rather than this night's nothing.
        return _build(
            pages,
            (),
            space_id=result.space_id,
            label=result.label,
            source_url=result.url,
            horizon_days=horizon_days,
            window_start=window_start,
            window_end=window_end,
            now=now,
            problem=JsonLdProblem.SEED_FETCH_FAILED,
            error=(
                f"{where}: all {len(followed)} per-event page(s) failed. "
                + "; ".join(notes)
            ),
            seed_urls=links,
            followed=followed,
            skipped_seeds=skipped,
        )

    return _build(
        pages,
        events,
        space_id=result.space_id,
        label=result.label,
        source_url=result.url,
        horizon_days=horizon_days,
        window_start=window_start,
        window_end=window_end,
        now=now,
        error="; ".join(notes) or None,
        seed_urls=links,
        followed=followed,
        skipped_seeds=skipped,
        # "Truncated" means links were left unread because of the cap — a
        # deliberate, reported ceiling. A link skipped as off-host or as a
        # robots-disallowed route is not truncation; it is the rule working.
        truncated=any("cap" in why for _url, why in skipped),
    )


def parse_jsonld_text(
    document: str,
    *,
    space_id: str = "",
    label: str = "",
    source_url: str = "",
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    today: dt.date | None = None,
    now: dt.datetime | None = None,
) -> JsonLdParse:
    """Parse **one HTML document** for events. The half with no HTTP in it.

    Split out for the same reason ``ics`` splits ``parse_ics_text``: the tests,
    and a human diffing two files out of ``raw/``, must be able to reach the
    parser without manufacturing a :class:`~pipeline.fetch.FetchResult`. It
    never follows a seed list — there is no fetcher to follow it with.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    today = today or now.date()
    window_start = today
    window_end = today + dt.timedelta(days=horizon_days)
    where = f"{space_id}:{label}" if space_id or label else "<jsonld>"

    decoded = _decode_page(document, url=source_url, where=where)
    if not decoded.page.ok:
        return _failure(
            decoded.page.problem,
            decoded.page.note or "document unreadable",
            space_id=space_id,
            label=label,
            source_url=source_url,
            horizon_days=horizon_days,
            window_start=window_start,
            window_end=window_end,
            now=now,
            pages=[decoded.page],
        )
    return _build(
        [decoded.page],
        decoded.events,
        space_id=space_id,
        label=label,
        source_url=source_url,
        horizon_days=horizon_days,
        window_start=window_start,
        window_end=window_end,
        now=now,
        error=decoded.page.note,
    )


# --------------------------------------------------------------------------- fetching


def fetch_jsonld(
    fetcher: Fetcher,
    ref: SourceRef,
    *,
    horizon_days: int | None = None,
    today: dt.date | None = None,
    now: dt.datetime | None = None,
    conditional: bool = False,
    strict_content_type: bool = True,
    max_seeds: int = MAX_SEED_FETCHES,
) -> JsonLdParse:
    """Fetch a registered source and follow its seed list. All in one call.

    The counterpart to :func:`pipeline.adapters.tribe_rest.fetch_tribe_rest`,
    and the entry point for the repair workflow: one source, every page, no run.

    ``conditional`` defaults to **off**: a 304 on the seed feed would leave no
    item links to follow, and the nightly run reaches this adapter through
    :func:`pipeline.cli.process_source`, which applies the registry's own
    conditional-GET policy to the first request.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    today = today or now.date()
    if horizon_days is None:
        horizon_days = fetcher.registry.horizon_days

    _space, source = ref
    result = fetcher.fetch_url(ref, request_url(source), conditional=conditional)
    return parse_jsonld(
        result,
        horizon_days=horizon_days,
        today=today,
        now=now,
        strict_content_type=strict_content_type,
        fetcher=fetcher,
        ref=ref,
        max_seeds=max_seeds,
    )


#: The adapter entry point named by ``sources.yaml``'s ``adapter: jsonld``.
parse = parse_jsonld


__all__ = [
    "CONTAINER_KEYS",
    "DEFAULT_ZONE",
    "DISALLOWED_QUERY_FORMATS",
    "EVENT_TYPES",
    "MAX_SEED_FETCHES",
    "JsonLdEvent",
    "JsonLdPage",
    "JsonLdParse",
    "JsonLdProblem",
    "collect_types",
    "fetch_jsonld",
    "is_disallowed_route",
    "is_event_type",
    "iter_events",
    "ld_blocks",
    "ld_items",
    "looks_like_feed",
    "looks_like_html",
    "parse",
    "parse_jsonld",
    "parse_jsonld_text",
    "parse_ld_datetime",
    "seed_links",
]
