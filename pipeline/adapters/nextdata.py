"""``__NEXT_DATA__`` on Eventbrite **organizer** pages (``/o/<slug>-<id>``).

Eventbrite has had no usable public search API since 2019 and its organizer
profiles no longer emit JSON-LD: they are a Next.js app, and the events live
only in ``<script id="__NEXT_DATA__" type="application/json">`` at
``props.pageProps.upcomingEvents``, counted by
``props.pageProps.upcomingEventsTotal``.

Why this adapter exists at all
------------------------------

**Because of a correction, and the correction is the whole lesson.** Humanmade's
Eventbrite source was registered as ``jsonld``. Since Eventbrite's migration to
Next.js that page carries **zero** ``application/ld+json`` blocks, so the source
would have returned an empty list every night, forever, without ever erroring —
a green run, a healthy-looking report, and a space quietly missing from the
calendar. *A wrong adapter is as silent as a wrong URL.* That is why every
judgement below is a **reported** problem with its own name rather than an empty
list: the only zero this module is willing to publish is one the page itself
declared.

Note the boundary carefully, because it is easy to over-correct: individual
Eventbrite **event** pages (``/e/<slug>-tickets-<id>``) still emit clean
``schema.org/EducationEvent`` JSON-LD and are handled by ``jsonld`` (issue
0020). It is only the **organizer listing** that moved. Both are true at once,
and confusing them costs a space in either direction.

Built on ``embedded_json``, deliberately
----------------------------------------

Issue 0021 designed :mod:`pipeline.adapters.embedded_json` so this module could
be a thin configuration of it rather than a second parser, and that is what this
is: :data:`EVENTBRITE_ORGANIZER_FIELD_MAP` is an
:class:`~pipeline.adapters.embedded_json.FieldMap` with a dotted
:attr:`~pipeline.adapters.embedded_json.FieldMap.items` path, and the item walk,
the UID rule, the horizon clip and the counting are all
``embedded_json``'s. ``script_id`` is hardcoded to ``__NEXT_DATA__`` because
that is what makes this adapter *this* adapter; a source that needs a different
blob name is an ``embedded_json`` source.

Two things are genuinely Eventbrite-specific and are the only real code here.

**1. A start is three fields, not one.** ``start_date`` (``"2026-08-20"``),
``start_time`` (``"11:00"``) and ``timezone`` (``"America/Los_Angeles"``) arrive
separately. :func:`assemble_start` joins the first two into one ISO wall-clock
string and the parse runs with the item's **own** zone, so an organizer whose
event sits in another timezone is converted with that timezone rather than with
this project's Pacific default. :attr:`~pipeline.adapters.ics.IcsEvent.tz` then
carries that IANA name — a resolvable one, because
:func:`pipeline.normalize.day_start_utc` and issue 0016's health filter both
call ``ZoneInfo`` on it and that filter runs *outside* the per-source
try/except. An item whose ``timezone`` is missing or unresolvable falls back to
:data:`~pipeline.adapters.embedded_json.DEFAULT_ZONE` and is **counted**
(:attr:`NextDataParse.assumed_timezone_count`), never assumed silently.

A ``start_date`` with **no** ``start_time`` is treated as undated and counted,
not as midnight: a tour published at 12:00 AM is wrong data that looks like good
data, and this project would rather report the gap.

**2. ``primary_venue.address`` is a real location.** The Crucible's Eventbrite
organizer is the only source in the whole registry that carries a postal address
for that space — the course catalog and the Store API have no location field of
any kind. The 2026-08-05 survey recorded the value (``1260 7th St, Oakland, CA
94607``, with lat/long) but not which sub-key holds it, so
:func:`venue_address` accepts the forms Eventbrite is known to use (a bare
string, ``localized_address_display``, the multi-line list, or the address parts)
and falls back to the venue *name*. Guessing one key and publishing ``None`` on
the others would be the silent-empty failure again, one field down.

Zero is an observation, not a failure
--------------------------------------

Three of the four registered consumers report ``upcomingEventsTotal: 0``, so
this adapter has to be able to say "zero, and I am sure" as clearly as it says
"something is wrong". It does that by **only** accepting a zero the page
declared:

- ``upcomingEvents: []`` **and** ``upcomingEventsTotal: 0`` →
  :attr:`NextDataProblem.NONE`, no events, :attr:`NextDataParse.reported_zero`
  true and :attr:`NextDataParse.parsed` true. Issue 0016's ``allow_zero`` /
  ``require_nonzero_once`` gates take it from there; nothing here trips them.
- ``props.pageProps`` present with **no** ``upcomingEvents`` key →
  :attr:`NextDataProblem.NO_EVENTS_KEY`. The shape changed under the same URL.
- ``upcomingEvents`` present with **no** ``upcomingEventsTotal`` →
  :attr:`NextDataProblem.NO_TOTAL`. Without the total, an empty array and a
  renamed field look identical, and this module refuses to guess which it is.
- The two disagreeing →	:attr:`NextDataProblem.COUNT_DISAGREES`, in both
  directions. A total of 7 with an empty array is not a quiet week.

Registered consumers and their state on 2026-08-05
--------------------------------------------------

=========================================  ====================================
``the-crucible`` / ``eventbrite-organizer``  **7 events**, all "Free Crucible
                                             Tour", 2026-08-20..2026-11-19.
                                             ``hasMoreUpcoming: false``. The
                                             only source for their free public
                                             tours and the only one with a
                                             location. ``trust: 70``.
``humanmade`` / ``eventbrite-organizer``     ``upcomingEventsTotal: 0``,
                                             confirmed three ways (plain curl,
                                             browser-UA curl, WebFetch).
                                             ``require_nonzero_once``.
``the-box-shop`` / ``eventbrite-organizer``  0, ``enabled: false``.
``lower-48``                                 0, commented out of the registry.
=========================================  ====================================

Rate limit and terms
--------------------

**One request per source, ever.** This adapter never follows anything: no second
page, no ``?tab=past`` (it returns the same blob — past events are client-
fetched), and no guess at the XHR behind ``hasMoreUpcoming``. When that flag is
true it is reported as a note, because inventing an undocumented internal
endpoint is exactly the behavior Eventbrite's terms are aimed at. The fetch
layer's 2 s/host limit applies unchanged.

Eventbrite's Terms restrict scraping. These are published organizer pages read
at very low volume with an honest User-Agent, and the recorded preference in
both ``spaces/humanmade.md`` and ``spaces/lower-48.md`` is the same: **ask the
space to publish somewhere else** — Humanmade already runs a public Luma
calendar — and treat this adapter as the fallback it is.

Implemented by issue 0023.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pipeline.adapters.embedded_json import (
    DEFAULT_ZONE,
    LENIENT_CONTENT_TYPES,
    EmbeddedJsonEvent,
    FieldMap,
    find_script,
    json_script_ids,
    looks_like_html,
    resolve_path,
)

# The item walk, the text helper, the tally and the sort order are
# ``embedded_json``'s, imported rather than copied — the same rule
# ``json_doc`` follows, for the same reason: a second copy of the UID rule or
# of the horizon clip would drift, and the copy that drifted would be the one
# nobody was reading when a space went quiet.
from pipeline.adapters.embedded_json import (  # noqa: PLC2701
    _ItemTally as ItemTally,
)
from pipeline.adapters.embedded_json import (  # noqa: PLC2701
    _build_events as build_events,
)
from pipeline.adapters.embedded_json import (  # noqa: PLC2701
    _clean as clean,
)
from pipeline.adapters.embedded_json import (  # noqa: PLC2701
    _sort_key as sort_key,
)
from pipeline.adapters.ics import DEFAULT_HORIZON_DAYS, IcsParse

# ``source_tz`` is written in exactly the form ``jsonld`` writes it, by the same
# function, so two adapters reading the same Eventbrite space cannot disagree
# about what "-07:00" looks like.
from pipeline.adapters.jsonld import (  # noqa: PLC2701
    _offset_text as offset_text,
)
from pipeline.config import SourceRef
from pipeline.fetch import (
    HTML_CONTENT_TYPES,
    FetchResult,
    Fetcher,
    Outcome,
    request_url,
)

# --------------------------------------------------------------------------- knobs

#: The blob's ``id``. Hardcoded, and that is the difference between this adapter
#: and ``embedded_json``: a source needing another name is an ``embedded_json``
#: source with a ``script_id``.
NEXT_DATA_SCRIPT_ID = "__NEXT_DATA__"

#: Where Next.js puts the page's server-rendered props, and where Eventbrite
#: puts the organizer's events inside them.
PAGE_PROPS_PATH = "props.pageProps"
EVENTS_KEY = "upcomingEvents"
TOTAL_KEY = "upcomingEventsTotal"
HAS_MORE_KEY = "hasMoreUpcoming"
EVENTS_PATH = f"{PAGE_PROPS_PATH}.{EVENTS_KEY}"

#: Per-item paths that the 2026-08-05 survey confirmed by fetching.
START_DATE_PATH = "start_date"
START_TIME_PATH = "start_time"
TIMEZONE_PATH = "timezone"
VENUE_PATH = "primary_venue"

#: Keys :func:`assemble_item` writes onto a copy of each item. Underscored so
#: they cannot collide with anything Eventbrite ships, and read back through the
#: ordinary :class:`~pipeline.adapters.embedded_json.FieldMap` paths — the
#: assembly is a hook, not a second parser.
ASSEMBLED_START = "_assembled_start"
ASSEMBLED_TITLE = "_assembled_title"
ASSEMBLED_LOCATION = "_assembled_location"
ASSEMBLED_DESCRIPTION = "_assembled_description"

#: A path no Eventbrite item carries, used where this source deliberately
#: publishes **nothing**. The organizer payload does have price-ish and tag-ish
#: fields, but the survey pinned neither, and a free tour showing a wrong price
#: is worse than a tour showing none. Ask, then map them.
UNMAPPED = "_not_in_this_payload"


# --------------------------------------------------------------------------- outcomes


class NextDataProblem(str, Enum):
    """Why a :class:`NextDataParse` is not usable. ``NONE`` means it is.

    The first five mirror :class:`~pipeline.adapters.ics.IcsProblem` so the
    CLI's ``record.problem`` stays one vocabulary. The rest are the failures
    only an organizer-page parse can have, and they are kept apart because "0
    events" from each of them means something different to a human reading
    ``health.json`` at 09:00 — and because exactly one of them is not a failure
    at all.
    """

    NONE = "none"
    TRANSPORT = "transport"
    NOT_MODIFIED = "not_modified"
    EMPTY_BODY = "empty_body"
    WRONG_CONTENT_TYPE = "wrong_content_type"

    #: Non-2xx on the registered URL.
    HTTP_ERROR = "http_error"
    #: The page parsed and carries no ``<script id="__NEXT_DATA__">``. Either
    #: Eventbrite changed its shell again, or this URL is not an organizer page
    #: (collection pages ``/cc/<slug>`` have no ``__NEXT_DATA__`` at all —
    #: recorded in ``spaces/the-crucible.md``).
    BLOB_NOT_FOUND = "blob_not_found"
    #: The blob was found and is not valid JSON.
    NOT_JSON = "not_json"
    #: Valid JSON with no ``props.pageProps`` object. Not a Next.js page shape.
    NO_PAGE_PROPS = "no_page_props"
    #: ``props.pageProps`` is there and ``upcomingEvents`` is not, or is not a
    #: list. The shape changed under the same URL — never an empty calendar.
    NO_EVENTS_KEY = "no_events_key"
    #: ``upcomingEvents`` is there and ``upcomingEventsTotal`` is not. Without
    #: the total there is no way to tell a real zero from a renamed field, and
    #: guessing in favour of zero is how a space disappears quietly.
    NO_TOTAL = "no_total"
    #: The total and the array contradict each other, in either direction.
    COUNT_DISAGREES = "count_disagrees"
    #: Events were listed and **not one** carried a usable start. A schema
    #: change, not a quiet week.
    NO_DATES = "no_dates"


# --------------------------------------------------------------------------- field map


#: Eventbrite's organizer shape, as an ``embedded_json`` field map.
#:
#: Everything the survey confirmed is a plain dotted path; the four
#: ``_assembled_*`` paths are filled by :func:`assemble_item` just before the
#: walk. ``start_format="iso"`` because ``"2026-08-20" + "T" + "11:00"`` *is*
#: ISO-8601 once joined, which is what makes the three-field start a hook and
#: not a parser.
EVENTBRITE_ORGANIZER_FIELD_MAP = FieldMap(
    items=EVENTS_PATH,
    # The Eventbrite event id. Stable, and the UID becomes
    # `{id}:{start_epoch}` -> `{space_id}:{id}:{start_epoch}` in normalize.py.
    # An item with no id falls back to normalize's sha1 and is counted.
    uid="id",
    title=ASSEMBLED_TITLE,
    url="url",
    starts=ASSEMBLED_START,
    start_format="iso",
    description=ASSEMBLED_DESCRIPTION,
    location=ASSEMBLED_LOCATION,
    image="image.url",
    # Deliberately unmapped — see UNMAPPED.
    categories=UNMAPPED,
    price=UNMAPPED,
    member_price=None,
    in_stock=None,
    hours=None,
    audience=None,
    level=None,
    item_format=None,
    time_of_day=None,
    days_text=None,
)


# --------------------------------------------------------------------------- result


@dataclass(frozen=True)
class NextDataParse(IcsParse):
    """The adapter's return value. Same surface as :class:`IcsParse`, plus what
    the organizer blob said about itself.

    Subclasses :class:`~pipeline.adapters.ics.IcsParse` so every consumer
    written against the ICS adapter — ``normalize_ics``, the CLI's
    ``SourceRecord``, issue 0016's gates — works unchanged.
    """

    problem: NextDataProblem = NextDataProblem.NONE  # type: ignore[assignment]

    script_id: str = NEXT_DATA_SCRIPT_ID
    #: Every ``application/json`` blob id on the page, in document order. What
    #: turns "the blob is missing" into a one-line diagnosis.
    script_ids_seen: tuple[str, ...] = ()
    blob_bytes: int = 0
    #: ``sha1(blob)[:16]``, for diffing two files out of ``raw/``.
    blob_digest: str = ""

    #: ``props.pageProps.upcomingEventsTotal`` exactly as the page reported it.
    #: ``None`` means the page did not report one, which is
    #: :attr:`NextDataProblem.NO_TOTAL` and never an assumed zero.
    reported_total: int | None = None
    #: ``props.pageProps.hasMoreUpcoming``. True means the page is holding
    #: events back behind a client-side request we deliberately do not make.
    has_more: bool | None = None

    #: Provenance from the same blob, so a run report can prove *which*
    #: organizer answered.
    organizer: str | None = None
    organizer_id: str | None = None

    #: Entries in ``upcomingEvents``. 7 for The Crucible, 0 for the rest.
    item_count: int = 0
    #: Items skipped for having no usable start — including a ``start_date``
    #: with no ``start_time``. Counted, never quietly absent.
    undated_item_count: int = 0
    undated_ids: tuple[str, ...] = ()
    #: Start values present and unusable.
    bad_date_count: int = 0
    #: Items with no ``id``; their UID falls back to normalize's sha1.
    missing_id_count: int = 0
    #: Items whose ``timezone`` was missing or unresolvable and which were read
    #: as :data:`~pipeline.adapters.embedded_json.DEFAULT_ZONE`.
    assumed_timezone_count: int = 0

    @property
    def ok(self) -> bool:
        """True when the blob was found, decoded, and understood.

        **Zero events with ``ok=True`` is a legitimate answer here** and is the
        normal state of three of the four registered consumers — but only when
        the page said so; see :attr:`reported_zero`.
        """
        return self.problem is NextDataProblem.NONE

    @property
    def parsed(self) -> bool:
        """The "this parsed fine" signal, independent of the event count.

        True when the named blob was on the page, decoded, and carried both
        ``upcomingEvents`` and ``upcomingEventsTotal``. This is what makes a
        zero from this adapter *readable*: ``parsed and not events`` is an
        organizer with nothing scheduled, while ``not parsed`` is a page that
        changed shape, and the two have entirely different repairs.
        """
        return self.ok and bool(self.blob_digest) and self.reported_total is not None

    @property
    def reported_zero(self) -> bool:
        """The page itself declared zero upcoming events, and we believe it."""
        return self.parsed and self.reported_total == 0 and not self.events

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        head = f"{self.space_id}:{self.label}" if self.space_id or self.label else "<nextdata>"
        if not self.ok:
            return f"{head} FAILED {self.problem.value}: {self.error}"
        if self.reported_zero:
            return f"{head} 0 events, and the page reports upcomingEventsTotal: 0"
        return (
            f"{head} {self.event_count} events in {self.horizon_days}d "
            f"(from {self.item_count} of {self.reported_total} upcoming in "
            f"#{self.script_id}, {self.blob_bytes}B)"
        )


# --------------------------------------------------------------------------- assembly


def _zone(name: str) -> ZoneInfo | None:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return None


def event_zone(item: dict[str, Any]) -> tuple[str, ZoneInfo | None, bool]:
    """``(IANA name, zone, assumed)`` for one upcoming event.

    Eventbrite writes the zone per event, so an organizer with an out-of-state
    event is converted with *that* event's zone. A missing or unresolvable
    value falls back to :data:`~pipeline.adapters.embedded_json.DEFAULT_ZONE`
    and says so, because ``tz`` has to be a resolvable IANA name downstream and
    a silent assumption about a timezone is how an event lands an hour wrong.
    """
    name = clean(resolve_path(item, TIMEZONE_PATH))
    if name:
        zone = _zone(name)
        if zone is not None:
            return name, zone, False
    return DEFAULT_ZONE, _zone(DEFAULT_ZONE), True


def assemble_start(item: dict[str, Any]) -> str | None:
    """``start_date`` + ``start_time`` -> one ISO wall-clock string.

    ``"2026-08-20"`` and ``"11:00"`` become ``"2026-08-20T11:00"``, which
    :func:`~pipeline.adapters.embedded_json.parse_iso` reads against the item's
    own zone. Nothing naive leaves this module: the zone is attached during that
    parse.

    A ``start_date`` with no ``start_time`` returns ``None`` and is counted as
    undated. Midnight would be a wrong time that looks like a right one, and
    every observed Eventbrite item carries both fields — so an item missing one
    is a signal, not a shrug. ``start.local`` is accepted as a fallback because
    other Eventbrite surfaces write the same wall clock that way.
    """
    date_text = clean(resolve_path(item, START_DATE_PATH))
    time_text = clean(resolve_path(item, START_TIME_PATH))
    if date_text and time_text:
        return f"{date_text}T{time_text}"
    return clean(resolve_path(item, "start.local"))


def venue_address(item: dict[str, Any]) -> str | None:
    """``primary_venue.address`` as one postal string, in whichever form it came.

    **The only real location in the registry for The Crucible**, which is why
    this tolerates four shapes rather than picking one. The survey recorded the
    value (``1260 7th St, Oakland, CA 94607``) but not the sub-key, and an
    adapter that guessed wrong would publish ``None`` here and hand the space's
    only address to :class:`pipeline.normalize.VenuePolicy` for no reason.

    Falls back to the venue **name** when there is no address at all: a named
    venue is still evidence, and ``VenuePolicy`` can act on it.
    """
    venue = resolve_path(item, VENUE_PATH)
    if not isinstance(venue, dict):
        return None

    address = venue.get("address")
    text = clean(address)
    if text:
        return text

    if isinstance(address, dict):
        display = clean(address.get("localized_address_display"))
        if display:
            return display
        lines = address.get("localized_multi_line_address_display")
        if isinstance(lines, list):
            joined = ", ".join(part for part in (clean(line) for line in lines) if part)
            if joined:
                return joined
        parts = [
            clean(address.get(key))
            for key in ("address_1", "address_2", "city", "region", "postal_code")
        ]
        joined = ", ".join(part for part in parts if part)
        if joined:
            return joined

    return clean(venue.get("name"))


def _text_field(item: dict[str, Any], *paths: str) -> str | None:
    """The first of several paths that yields text.

    Eventbrite writes a title as ``name`` on the organizer payload and as
    ``name.text`` on the API; both are read here rather than one being assumed,
    for the same reason :func:`venue_address` reads four address forms.
    """
    for path in paths:
        text = clean(resolve_path(item, path))
        if text:
            return text
    return None


def assemble_item(item: dict[str, Any]) -> dict[str, Any]:
    """One raw item plus the four ``_assembled_*`` keys the field map reads.

    A copy, never a mutation: the original dict came out of the blob and stays
    exactly as the page wrote it, so a human diffing ``raw/`` sees the source and
    not our edits.
    """
    return {
        **item,
        ASSEMBLED_START: assemble_start(item),
        ASSEMBLED_TITLE: _text_field(item, "name.text", "name", "title"),
        ASSEMBLED_LOCATION: venue_address(item),
        ASSEMBLED_DESCRIPTION: _text_field(item, "summary", "description.text", "description"),
    }


# --------------------------------------------------------------------------- building


def _build(
    tally: ItemTally,
    *,
    item_count: int,
    reported_total: int | None,
    has_more: bool | None,
    organizer: str | None,
    organizer_id: str | None,
    assumed_timezones: int,
    script_ids_seen: Sequence[str],
    blob_bytes: int,
    blob_digest: str,
    space_id: str,
    label: str,
    source_url: str,
    horizon_days: int,
    window_start: dt.date,
    window_end: dt.date,
    now: dt.datetime,
    problem: NextDataProblem = NextDataProblem.NONE,
    error: str | None = None,
) -> NextDataParse:
    """Assemble the walk into the returned :class:`NextDataParse`.

    Horizon clipping happens here, on the event's own local day, so the count
    this reports is the post-clip one issue 0016's gates read.
    """
    kept = [
        event
        for event in tally.events
        if window_start
        <= (event.start.date() if isinstance(event.start, dt.datetime) else event.start)
        <= window_end
    ]

    return NextDataParse(
        events=tuple(sorted(kept, key=sort_key)),
        problem=problem,
        error=error,
        space_id=space_id,
        label=label,
        source_url=source_url,
        # Raw = every start value seen, i.e. one per upcoming event here. The
        # health gates count `event_count`, which is the post-clip number.
        vevent_count=tally.offerings,
        recurring_vevent_count=0,
        horizon_days=horizon_days,
        window_start=window_start,
        window_end=window_end,
        parsed_at=now,
        script_ids_seen=tuple(script_ids_seen),
        blob_bytes=blob_bytes,
        blob_digest=blob_digest,
        reported_total=reported_total,
        has_more=has_more,
        organizer=organizer,
        organizer_id=organizer_id,
        item_count=item_count,
        undated_item_count=tally.undated,
        undated_ids=tuple(tally.undated_ids[:20]),
        bad_date_count=tally.bad_dates,
        missing_id_count=tally.missing_ids,
        assumed_timezone_count=assumed_timezones,
    )


def _failure(
    problem: NextDataProblem,
    error: str,
    *,
    space_id: str = "",
    label: str = "",
    source_url: str = "",
    script_ids_seen: Sequence[str] = (),
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    window_start: dt.date | None = None,
    window_end: dt.date | None = None,
    now: dt.datetime | None = None,
    blob_bytes: int = 0,
    blob_digest: str = "",
    reported_total: int | None = None,
    has_more: bool | None = None,
    organizer: str | None = None,
    organizer_id: str | None = None,
    item_count: int = 0,
) -> NextDataParse:
    """A reported failure. Never raised — see the ``ics`` module docstring."""
    return NextDataParse(
        problem=problem,
        error=error,
        space_id=space_id,
        label=label,
        source_url=source_url,
        script_ids_seen=tuple(script_ids_seen),
        horizon_days=horizon_days,
        window_start=window_start,
        window_end=window_end,
        parsed_at=now,
        blob_bytes=blob_bytes,
        blob_digest=blob_digest,
        reported_total=reported_total,
        has_more=has_more,
        organizer=organizer,
        organizer_id=organizer_id,
        item_count=item_count,
    )


def _walk(items: Sequence[dict[str, Any]]) -> tuple[ItemTally, int]:
    """Every upcoming event through ``embedded_json``'s item walk.

    ``(tally, assumed_timezone_count)``. The walk itself is not reimplemented:
    each item is assembled, handed to
    :func:`~pipeline.adapters.embedded_json._build_events` with the item's own
    zone, and the events it appended are then re-stamped with that zone's IANA
    name — ``embedded_json`` writes its module-level Pacific default there,
    which is right for its own consumer and not for an organizer whose event is
    somewhere else.
    """
    tally = ItemTally()
    assumed = 0
    for item in items:
        if not isinstance(item, dict):
            # A string or a null where an event object belongs. Counted with
            # the undated rather than skipped in silence, so `item_count`
            # always equals what the walk accounted for.
            tally.undated += 1
            continue
        zone_name, zone, was_assumed = event_zone(item)
        assumed += int(was_assumed)

        first = len(tally.events)
        build_events(
            assemble_item(item),
            field_map=EVENTBRITE_ORGANIZER_FIELD_MAP,
            script_id=NEXT_DATA_SCRIPT_ID,
            zone=zone,
            tally=tally,
        )
        for index in range(first, len(tally.events)):
            event: EmbeddedJsonEvent = tally.events[index]
            start = event.start
            tally.events[index] = replace(
                event,
                # A resolvable IANA name, and *this event's* name. Issue 0016's
                # health filter calls ZoneInfo on it outside the per-source
                # try/except, so it can never be an offset string.
                tz=zone_name if zone is not None else None,
                # The offset that zone was actually at, so the source's own
                # wall clock is recoverable from the record.
                source_tz=(
                    offset_text(start) if isinstance(start, dt.datetime) else None
                ),
                dtstart_form="tzid",
            )
    return tally, assumed


# --------------------------------------------------------------------------- decoding


def _transport_problem(result: FetchResult, *, where: str) -> tuple[NextDataProblem, str] | None:
    """Everything that can be wrong before the body is worth looking at."""
    if result.outcome is Outcome.NOT_MODIFIED:
        return (
            NextDataProblem.NOT_MODIFIED,
            f"{where}: HTTP 304, unchanged since the last run — reuse the stored "
            "events rather than treating this as zero",
        )
    if result.outcome is not Outcome.FETCHED:
        detail = f" ({result.reason})" if result.reason else ""
        return (NextDataProblem.TRANSPORT, f"{where}: {result.outcome.value}{detail}")

    status = result.status_code
    if status is not None and not 200 <= status < 300:
        return (NextDataProblem.HTTP_ERROR, f"{where}: HTTP {status}")
    if not result.has_body:
        return (NextDataProblem.EMPTY_BODY, f"{where}: HTTP {status} with an empty body")
    return None


def _decode(
    document: str,
    *,
    where: str,
    space_id: str = "",
    label: str = "",
    source_url: str = "",
    horizon_days: int,
    window_start: dt.date,
    window_end: dt.date,
    now: dt.datetime,
) -> NextDataParse:
    """Read one organizer page. Never raises.

    Every judgement happens here, in order: is the blob on the page, is it JSON,
    is it a Next.js payload, does it carry the events key, does it carry the
    total, do those two agree, and did anything carry a date. Seven questions,
    seven different repairs — and exactly one of the answers ("zero, and the
    page says zero") is not a problem at all.
    """

    def fail(
        problem: NextDataProblem,
        error: str,
        **extra: Any,
    ) -> NextDataParse:
        return _failure(
            problem,
            error,
            space_id=space_id,
            label=label,
            source_url=source_url,
            horizon_days=horizon_days,
            window_start=window_start,
            window_end=window_end,
            now=now,
            **extra,
        )

    script, scripts = find_script(document, NEXT_DATA_SCRIPT_ID)
    seen = json_script_ids(scripts)

    if script is None:
        wrong_type = next(
            (item for item in scripts if item.id == NEXT_DATA_SCRIPT_ID and not item.is_json),
            None,
        )
        detail = (
            f" A <script id=\"{NEXT_DATA_SCRIPT_ID}\"> is present but its type is "
            f"{wrong_type.type!r}, not application/json."
            if wrong_type
            else ""
        )
        return fail(
            NextDataProblem.BLOB_NOT_FOUND,
            f"{where}: no <script type=\"application/json\" id=\"{NEXT_DATA_SCRIPT_ID}\"> "
            f"on the page.{detail} application/json blob ids present: "
            f"{', '.join(seen) or '<none>'}. This is a drifted source or the wrong "
            "kind of Eventbrite page — organizer pages (/o/<slug>-<id>) carry the "
            "blob, collection pages (/cc/<slug>) do not, and individual event pages "
            "still emit JSON-LD instead (issue 0020). An adapter that returned [] "
            "here would look exactly like an organizer with nothing scheduled",
            script_ids_seen=seen,
        )

    blob = script.text
    blob_bytes = script.byte_count
    digest = hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]

    try:
        payload = json.loads(blob)
    except ValueError as exc:
        head = blob.strip()[:60].replace("\n", " ")
        return fail(
            NextDataProblem.NOT_JSON,
            f"{where}: <script id=\"{NEXT_DATA_SCRIPT_ID}\"> is {blob_bytes} bytes and "
            f"is not valid JSON ({exc}); it starts {head!r}",
            script_ids_seen=seen,
            blob_bytes=blob_bytes,
        )

    page_props = resolve_path(payload, PAGE_PROPS_PATH)
    if not isinstance(page_props, dict):
        shape = (
            f"a {type(payload).__name__} with top-level keys "
            f"{', '.join(sorted(map(str, payload))[:12]) or '<none>'}"
            if isinstance(payload, dict)
            else f"a {type(payload).__name__}"
        )
        return fail(
            NextDataProblem.NO_PAGE_PROPS,
            f"{where}: <script id=\"{NEXT_DATA_SCRIPT_ID}\"> decoded to {shape} and has "
            f"no {PAGE_PROPS_PATH!r} object. That is not the Next.js payload this "
            "adapter reads; the page shell changed",
            script_ids_seen=seen,
            blob_bytes=blob_bytes,
            blob_digest=digest,
        )

    organizer = clean(resolve_path(page_props, "organizer.name"))
    organizer_id = clean(resolve_path(page_props, "organizer.id"))
    has_more_raw = page_props.get(HAS_MORE_KEY)
    has_more = has_more_raw if isinstance(has_more_raw, bool) else None

    def shape_failure(problem: NextDataProblem, error: str, **extra: Any) -> NextDataParse:
        return fail(
            problem,
            error,
            script_ids_seen=seen,
            blob_bytes=blob_bytes,
            blob_digest=digest,
            has_more=has_more,
            organizer=organizer,
            organizer_id=organizer_id,
            **extra,
        )

    raw_events = page_props.get(EVENTS_KEY)
    if not isinstance(raw_events, list):
        found = "is missing" if EVENTS_KEY not in page_props else (
            f"is a {type(raw_events).__name__}, not a list"
        )
        return shape_failure(
            NextDataProblem.NO_EVENTS_KEY,
            f"{where}: {PAGE_PROPS_PATH}.{EVENTS_KEY} {found}. pageProps keys present: "
            f"{', '.join(sorted(map(str, page_props))[:15]) or '<none>'}. The blob is "
            "still there and its shape changed, which is a different repair from an "
            "organizer with nothing scheduled — and returning [] here is exactly how "
            "a space disappears from the calendar without anything erroring",
        )

    raw_total = page_props.get(TOTAL_KEY)
    total = raw_total if isinstance(raw_total, int) and not isinstance(raw_total, bool) else None
    if total is None:
        return shape_failure(
            NextDataProblem.NO_TOTAL,
            f"{where}: {PAGE_PROPS_PATH}.{EVENTS_KEY} carries {len(raw_events)} entry/"
            f"entries and {TOTAL_KEY} is "
            f"{'missing' if TOTAL_KEY not in page_props else repr(raw_total)}. The total "
            "is what makes an empty array *readable*: without it, a real zero and a "
            "renamed field look identical, and this adapter will not guess in favour "
            "of zero",
            item_count=len(raw_events),
        )

    if total != len(raw_events) and (not raw_events or total == 0):
        # Both directions of the contradiction, and neither is publishable: a
        # total of 7 with an empty array is a hydration or shape problem, and a
        # total of 0 with items in the array means we are reading the wrong
        # count. A short array with a positive total is *not* here — that is
        # `hasMoreUpcoming`, and it is a note further down.
        return shape_failure(
            NextDataProblem.COUNT_DISAGREES,
            f"{where}: {TOTAL_KEY} is {total} and {EVENTS_KEY} carries "
            f"{len(raw_events)} entry/entries. The page contradicts itself, so neither "
            "number is trustworthy; refusing to publish either as this organizer's "
            "calendar",
            reported_total=total,
            item_count=len(raw_events),
        )

    notes: list[str] = []
    if total == 0:
        # **The legitimate zero, and the whole reason this branch is explicit.**
        # Three of the four registered consumers live here. It is `ok`, it is
        # `parsed`, and `reported_zero` says a human can trust it — issue 0016's
        # allow_zero / require_nonzero_once gates decide what to do about it.
        return _build(
            ItemTally(),
            item_count=0,
            reported_total=0,
            has_more=has_more,
            organizer=organizer,
            organizer_id=organizer_id,
            assumed_timezones=0,
            script_ids_seen=seen,
            blob_bytes=blob_bytes,
            blob_digest=digest,
            space_id=space_id,
            label=label,
            source_url=source_url,
            horizon_days=horizon_days,
            window_start=window_start,
            window_end=window_end,
            now=now,
            error=(
                f"{where}: the page reports {TOTAL_KEY}: 0 with an empty {EVENTS_KEY} "
                "array. This is an observation, not a parse failure — the blob was "
                "found, decoded and understood; the organizer simply has nothing "
                "scheduled"
            ),
        )

    tally, assumed = _walk(raw_events)

    if not tally.events:
        return shape_failure(
            NextDataProblem.NO_DATES,
            f"{where}: {len(raw_events)} upcoming event(s) and not one usable start "
            f"({tally.undated} with no {START_DATE_PATH!r}+{START_TIME_PATH!r} pair, "
            f"{tally.bad_dates} unusable value(s)). The page says {total} upcoming, so "
            "this is a schema change and not an empty organizer",
            reported_total=total,
            item_count=len(raw_events),
        )

    if has_more:
        notes.append(
            f"{HAS_MORE_KEY} is true: the page is holding events back behind a "
            "client-side request this adapter deliberately does not make. Ask the "
            "space for a real feed rather than guessing at an internal endpoint"
        )
    if total != len(raw_events):
        notes.append(
            f"{TOTAL_KEY} says {total} and {EVENTS_KEY} carries {len(raw_events)}; "
            "not a gate, but worth a look"
        )
    if tally.undated:
        notes.append(
            f"{tally.undated} of {len(raw_events)} upcoming event(s) carry no usable "
            f"{START_DATE_PATH!r}+{START_TIME_PATH!r} pair and were skipped"
            + (f" ({', '.join(tally.undated_ids[:5])})" if tally.undated_ids else "")
        )
    if tally.bad_dates:
        notes.append(
            f"{tally.bad_dates} start value(s) were not a usable date and were dropped"
        )
    if tally.missing_ids:
        notes.append(
            f"{tally.missing_ids} event(s) had no 'id' — their UID falls back to "
            "sha1(space + start + title) in normalize.py"
        )
    if assumed:
        notes.append(
            f"{assumed} event(s) carried no resolvable {TIMEZONE_PATH!r} and were read "
            f"as {DEFAULT_ZONE}"
        )

    return _build(
        tally,
        item_count=len(raw_events),
        reported_total=total,
        has_more=has_more,
        organizer=organizer,
        organizer_id=organizer_id,
        assumed_timezones=assumed,
        script_ids_seen=seen,
        blob_bytes=blob_bytes,
        blob_digest=digest,
        space_id=space_id,
        label=label,
        source_url=source_url,
        horizon_days=horizon_days,
        window_start=window_start,
        window_end=window_end,
        now=now,
        error="; ".join(notes) or None,
    )


# --------------------------------------------------------------------------- parsing


def parse_nextdata(
    result: FetchResult,
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    today: dt.date | None = None,
    now: dt.datetime | None = None,
    strict_content_type: bool = True,
) -> NextDataParse:
    """Parse a fetched Eventbrite organizer page. Never raises.

    ``result`` is the registered page, already fetched by the caller. Nothing
    here is configurable from ``sources.yaml``: the blob name and the payload
    paths are what make this adapter Eventbrite's, so it needs neither the
    ``Fetcher`` nor the ``SourceRef``.

    A 404, a rendered error page, a shell change, a renamed key and a
    self-contradicting count all come back as a :class:`NextDataParse` with
    ``ok=False`` and a distinct :class:`NextDataProblem`. A declared zero comes
    back with ``ok=True`` and no events.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    today = today or now.date()
    window_start = today
    window_end = today + dt.timedelta(days=horizon_days)
    where = f"{result.source_key} ({result.url})"

    transport = _transport_problem(result, where=where)
    if transport is not None:
        return _failure(
            transport[0],
            transport[1],
            space_id=result.space_id,
            label=result.label,
            source_url=result.url,
            horizon_days=horizon_days,
            window_start=window_start,
            window_end=window_end,
            now=now,
        )

    text = result.text
    if strict_content_type and result.content_type is not None:
        if not result.content_type_is(*HTML_CONTENT_TYPES):
            tolerable = result.content_type_is(*LENIENT_CONTENT_TYPES) or looks_like_html(text)
            if not tolerable:
                return _failure(
                    NextDataProblem.WRONG_CONTENT_TYPE,
                    f"{where}: expected {' or '.join(HTML_CONTENT_TYPES)}, got "
                    f"{result.content_type} — HTTP 200 is not success, and a body that "
                    "is not the page we registered must not be read as an organizer "
                    "with nothing scheduled",
                    space_id=result.space_id,
                    label=result.label,
                    source_url=result.url,
                    horizon_days=horizon_days,
                    window_start=window_start,
                    window_end=window_end,
                    now=now,
                )

    return _decode(
        text,
        where=where,
        space_id=result.space_id,
        label=result.label,
        source_url=result.final_url or result.url,
        horizon_days=horizon_days,
        window_start=window_start,
        window_end=window_end,
        now=now,
    )


def parse_nextdata_text(
    document: str,
    *,
    space_id: str = "",
    label: str = "",
    source_url: str = "",
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    today: dt.date | None = None,
    now: dt.datetime | None = None,
) -> NextDataParse:
    """Parse **one HTML document**. The half with no HTTP in it.

    Split out for the same reason ``ics`` splits ``parse_ics_text``: the tests,
    and a human diffing two files out of ``raw/``, must be able to reach the
    parser without manufacturing a :class:`~pipeline.fetch.FetchResult`.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    today = today or now.date()
    return _decode(
        document,
        where=f"{space_id}:{label}" if space_id or label else "<nextdata>",
        space_id=space_id,
        label=label,
        source_url=source_url,
        horizon_days=horizon_days,
        window_start=today,
        window_end=today + dt.timedelta(days=horizon_days),
        now=now,
    )


# --------------------------------------------------------------------------- fetching


def fetch_nextdata(
    fetcher: Fetcher,
    ref: SourceRef,
    *,
    horizon_days: int | None = None,
    today: dt.date | None = None,
    now: dt.datetime | None = None,
    conditional: bool = False,
    strict_content_type: bool = True,
) -> NextDataParse:
    """Fetch a registered organizer page and parse it. All in one call.

    The counterpart to
    :func:`pipeline.adapters.embedded_json.fetch_embedded_json`, and the entry
    point for the repair workflow: one source, one page, no run.

    **Exactly one request goes out.** No ``?tab=past`` (it answers with the same
    blob — past events are fetched client-side), no page parameter, and no
    attempt at whatever XHR serves ``hasMoreUpcoming``. Eventbrite's terms
    restrict scraping and the standing recommendation for every consumer of this
    adapter is to ask the space to publish elsewhere; one polite, rate-limited
    GET of a public page is the whole budget.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    today = today or now.date()
    if horizon_days is None:
        horizon_days = fetcher.registry.horizon_days

    _space, source = ref
    result = fetcher.fetch_url(ref, request_url(source), conditional=conditional)
    return parse_nextdata(
        result,
        horizon_days=horizon_days,
        today=today,
        now=now,
        strict_content_type=strict_content_type,
    )


#: The adapter entry point named by ``sources.yaml``'s ``adapter: nextdata``.
parse = parse_nextdata


__all__ = [
    "ASSEMBLED_DESCRIPTION",
    "ASSEMBLED_LOCATION",
    "ASSEMBLED_START",
    "ASSEMBLED_TITLE",
    "EVENTBRITE_ORGANIZER_FIELD_MAP",
    "EVENTS_KEY",
    "EVENTS_PATH",
    "HAS_MORE_KEY",
    "NEXT_DATA_SCRIPT_ID",
    "PAGE_PROPS_PATH",
    "TOTAL_KEY",
    "NextDataParse",
    "NextDataProblem",
    "assemble_item",
    "assemble_start",
    "event_zone",
    "fetch_nextdata",
    "parse",
    "parse_nextdata",
    "parse_nextdata_text",
    "venue_address",
]
