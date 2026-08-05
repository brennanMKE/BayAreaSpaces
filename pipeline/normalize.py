"""Timezone discipline, stable UIDs, and the canonical event record.

Turns adapter output into the canonical event schema (Part 3 of
``maker-calendar-handoff.md``), and is the chokepoint where the two invariants
that quietly ruin the calendar are enforced.

Responsibilities:

- **Never let a naive datetime past this module.** Parse to an aware datetime
  immediately, store UTC, carry the original tz string alongside. Fail the run
  rather than guessing a zone. Python leaves this to discipline where Go's
  ``time.Time`` would enforce it structurally, so :func:`to_utc` and
  :meth:`Event.__post_init__` *are* that enforcement.
- **Stable UIDs**, including after RRULE expansion — see below, this is the
  part the documentation used to get wrong.
- Handle ``VALUE=DATE`` all-day events as a distinct case rather than coercing
  a :class:`datetime.date` into a :class:`datetime.datetime` and hoping.
- Apply ``space.address_override`` without silently relocating a genuinely
  off-site event.
- Drop events outside a sane window, and **quarantine rather than drop** the
  two shapes that mean "the extraction went wrong", so nothing disappears
  without leaving a record.

Filters from ``sources.yaml`` (``location_contains`` and friends) are issue
0010 and deliberately live downstream of this module: everything here is
source-agnostic, and a filter needs the canonical record this module produces.

Why an exception and not ``assert``
-----------------------------------

``python -O`` strips ``assert``. An invariant whose enforcement can be turned
off by a flag on the interpreter is not an invariant, and this job runs
unattended at 03:15. :class:`NaiveDatetimeError` is raised explicitly and
always.

The floating-time policy, stated once
-------------------------------------

Issue 0007 passes floating times — no ``TZID``, no ``X-WR-TIMEZONE`` — through
**naive on purpose**, so that they arrive here rather than being silently
resolved in the adapter. The policy:

**A floating time is interpreted in the source's declared timezone, never
rejected — but only through an explicit, logged conversion.** The zone is
taken, in order, from the calendar's ``X-WR-TIMEZONE``, then the space's
registry timezone, then ``defaults.timezone`` (``America/Los_Angeles``). Every
such conversion is logged at ``WARNING`` and recorded on the result as a
:class:`TimezoneConversion`, and the event is stamped ``tz_assumed=True`` so a
later stage can tell an assumed instant from a stated one.

The strictness is in what does *not* qualify as floating. A naive datetime
arriving while the adapter **declares** a zone (``source_tz`` set, or
``dtstart_form`` of ``utc``/``tzid``) is a contradiction — two statements that
cannot both be true — and raises. Guessing which one to believe is exactly the
class of bug this invariant exists to prevent. ``allow_floating=False`` turns
the assumption itself into a hard failure for operators who would rather lose
an event than publish a shifted one.

The UID rule, corrected
-----------------------

A source UID is **not** unique after RRULE expansion. Every occurrence of a
series carries the same ``UID`` — one Sudo Room series has 17 occurrences
sharing one — so namespacing on ``{space_id}:{source_uid}`` alone collapses a
whole series into a single event. That failure presents as a working feed with
suspiciously few events, never as an error, which is why it survived in the
documentation for as long as it did.

- Source UID, non-recurring: ``{space_id}:{source_uid}``.
- Source UID, recurring: ``{space_id}:{source_uid}:{start_utc}``.
- No source UID: ``sha1(space_id + start_utc + normalize(title))[:16]``.

Recurrence is read from :attr:`~pipeline.adapters.ics.IcsEvent.recurring`,
which the adapter takes off the *unexpanded* VEVENT. ``RECURRENCE-ID`` is set
on every expanded occurrence including non-recurring ones and therefore cannot
be used to detect a series.

Never include a scrape timestamp, page position, or any model output in a UID.

Per-source UID rules for the adapters still to come are accommodated by
:func:`make_uid` taking a ``source_uid`` rather than an ICS component: Maker
Nexus passes its JSON cache key ``YYYY-MM-DD_<amiliaId>``, The Crucible passes
``{id}:{start_epoch}``, Meetup's iCal passes ``event_<id>@meetup.com``. All
three already carry the occurrence, so all three are non-recurring by this
module's reckoning and keep the stable two-part form.

Implemented by issue 0009. Issue 0010 adds the registry filters on top.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import re
import unicodedata
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pipeline.adapters.ics import IcsEvent, IcsParse
from pipeline.config import Space

LOG = logging.getLogger("pipeline.normalize")

# --------------------------------------------------------------------------- knobs

#: Last-resort zone for a floating time. Matches ``defaults.timezone`` in
#: ``sources.yaml``; the registry's own value wins when one is passed in.
DEFAULT_TIMEZONE = "America/Los_Angeles"

UTC = dt.timezone.utc

#: Drop anything starting further out than this. Sudo Room's export is
#: pre-expanded to 2058 and Noisebridge's dead gCal invents events forever;
#: the horizon clip in the adapter is the real defense, this is the backstop
#: for adapters that have no horizon of their own.
MAX_FUTURE_DAYS = 730

#: Titles longer than this are quarantined, not published. A 200+ character
#: "title" is the classic signature of an LLM extraction that swallowed the
#: description, and it is never a real event name.
MAX_TITLE_CHARS = 200

#: Length of the hashed UID for sources that supply none. From the handoff.
_UID_HASH_CHARS = 16


# --------------------------------------------------------------------------- errors


class NormalizeError(Exception):
    """Base class for a normalization failure. Always fatal to the run."""


class NaiveDatetimeError(NormalizeError):
    """A naive datetime reached this module.

    The enforcement of CLAUDE.md's "never let a naive datetime past
    ``normalize.py``". Raised rather than asserted so ``python -O`` cannot
    switch the invariant off.
    """


class UnknownTimezoneError(NormalizeError):
    """A source or the registry named a zone the system has no data for."""


# --------------------------------------------------------------------------- verdicts


class DropReason(str, Enum):
    """Why an event was dropped. Dropped events are counted, never silent."""

    PAST = "past"
    TOO_FAR = "too_far"
    NO_START = "no_start"


class QuarantineReason(str, Enum):
    """Why an event was withheld from publication but kept for inspection."""

    EMPTY_TITLE = "empty_title"
    TITLE_TOO_LONG = "title_too_long"


class AddressSource(str, Enum):
    """Where :attr:`Event.address` came from."""

    NONE = "none"
    SOURCE = "source"
    OVERRIDE = "override"


# --------------------------------------------------------------------------- input


@dataclass(frozen=True)
class RawEvent:
    """One occurrence as an adapter saw it, in the source's own terms.

    The seam between *any* adapter and this module. ICS gets there through
    :func:`from_ics_event`; the JSON, JSON-LD, Tribe REST and RSS adapters
    still to be written construct one directly rather than pretending to be
    iCalendar. Nothing here is normalized yet — that is the whole point.
    """

    source_uid: str | None = None
    title: str | None = None

    #: ``datetime.date`` when :attr:`all_day`, otherwise ``datetime.datetime``.
    start: dt.date | dt.datetime | None = None
    #: For all-day events the **inclusive** last day (the adapter has already
    #: undone RFC 5545's exclusive ``DTEND``).
    end: dt.date | dt.datetime | None = None

    all_day: bool = False
    multi_day: bool = False
    days: int = 1

    #: Zone attached to :attr:`start` after parsing, if any.
    tz: str | None = None
    #: What the *source* declared: a ``TZID`` value, ``"UTC"`` for bare-``Z``,
    #: or ``None`` for floating and all-day.
    source_tz: str | None = None
    #: ``"date"`` | ``"utc"`` | ``"tzid"`` | ``"floating"`` | ``"unknown"``.
    dtstart_form: str = "unknown"

    #: True when the *unexpanded* source event carried an ``RRULE``/``RDATE``.
    #: Not ``RECURRENCE-ID``, which is set on every expanded occurrence.
    recurring: bool = False

    location: str | None = None
    description: str | None = None
    url: str | None = None
    categories: tuple[str, ...] = ()
    price: str | None = None
    rrule: str | None = None


def from_ics_event(event: IcsEvent) -> RawEvent:
    """Adapt issue 0007's :class:`~pipeline.adapters.ics.IcsEvent`.

    A field rename and nothing else: no conversion happens here, so a floating
    time is still naive when it reaches :func:`normalize_event` and still
    trips the policy above.
    """
    return RawEvent(
        source_uid=event.uid,
        title=event.title,
        start=event.start,
        end=event.end,
        all_day=event.all_day,
        multi_day=event.multi_day,
        days=event.days,
        tz=event.tz,
        source_tz=event.source_tz,
        dtstart_form=event.dtstart_form,
        recurring=event.recurring,
        location=event.location,
        description=event.description,
        url=event.url,
        categories=event.categories,
    )


# --------------------------------------------------------------------------- output


@dataclass(frozen=True)
class Event:
    """The canonical event record. Part 3 of the handoff, as a class.

    The first block of fields is the published schema verbatim; the second is
    provenance the later stages need and that would otherwise have to be
    recomputed by re-reading the source. :meth:`as_dict` emits the whole thing
    with datetimes rendered as ISO-8601 strings, which is the wire form the
    schema describes.

    ``start_utc`` and ``end_utc`` are aware :class:`datetime.datetime` values
    in UTC — always, on every instance, enforced in :meth:`__post_init__`.
    ``tz`` carries the original zone so the wall-clock time can be recovered.
    """

    # -- the canonical schema --------------------------------------------------
    uid: str
    space_id: str
    source_label: str
    title: str
    start_utc: dt.datetime
    end_utc: dt.datetime | None
    tz: str
    all_day: bool = False
    location_name: str | None = None
    address: str | None = None
    url: str | None = None
    #: Source text, never parsed to a float. "$45 / $30 members" is the truth.
    price: str | None = None
    description: str | None = None
    #: Source categories at this stage; ``enrich`` (issue 0013) assigns the
    #: published taxonomy. Issue 0010's ``categories_exclude`` filter runs on
    #: these, so they must survive normalization untouched.
    categories: tuple[str, ...] = ()
    #: Model-assigned, filled by ``enrich``. Never part of the UID.
    summary_line: str | None = None
    rrule: str | None = None
    #: Set to the run timestamp here; the SQLite store (issue 0014) preserves
    #: the true ``first_seen`` when it merges on ``uid``.
    first_seen: dt.datetime | None = None
    last_seen: dt.datetime | None = None
    #: Content fingerprint for change detection. Deliberately excludes the UID,
    #: the run timestamps and anything a model wrote.
    content_hash: str = ""

    # -- provenance ------------------------------------------------------------
    #: The source UID verbatim, before namespacing. Dedupe (issue 0012) matches
    #: on it across sources that syndicate the same event.
    source_uid: str | None = None
    dtstart_form: str = "unknown"
    #: True when :attr:`tz` was supplied by policy rather than by the source.
    tz_assumed: bool = False
    recurring: bool = False
    multi_day: bool = False
    days: int = 1
    #: All-day events only: the local start and **inclusive** end dates, kept
    #: so ``emit`` can write ``VALUE=DATE`` back without inverting the UTC math.
    start_date: dt.date | None = None
    end_date: dt.date | None = None
    address_source: AddressSource = AddressSource.NONE
    #: True when an ``address_override`` existed and was deliberately not
    #: applied because the event names a different venue.
    off_site: bool = False
    #: Set on a quarantined record, ``None`` on a publishable one.
    quarantine: QuarantineReason | None = None

    def __post_init__(self) -> None:
        # The last chokepoint. Everything upstream is careful; this is what
        # makes carelessness impossible rather than unlikely.
        for name in ("start_utc", "end_utc", "first_seen", "last_seen"):
            value = getattr(self, name)
            if value is None:
                continue
            if not isinstance(value, dt.datetime):
                raise NaiveDatetimeError(
                    f"{self.uid}: {name} is {type(value).__name__}, not a datetime"
                )
            if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
                raise NaiveDatetimeError(
                    f"{self.uid}: {name} is naive ({value.isoformat()}). A naive "
                    "datetime must never reach the canonical record — see the "
                    "timezone invariant in CLAUDE.md."
                )

    # -- conveniences ----------------------------------------------------------

    @property
    def start_local(self) -> dt.datetime:
        """:attr:`start_utc` back in :attr:`tz` — the source's wall clock."""
        return self.start_utc.astimezone(_zone(self.tz))

    @property
    def is_quarantined(self) -> bool:
        return self.quarantine is not None

    def as_dict(self) -> dict[str, Any]:
        """The record as plain JSON-ready data, datetimes as ISO-8601 strings."""
        data = asdict(self)
        for key, value in list(data.items()):
            if isinstance(value, dt.datetime):
                data[key] = value.isoformat()
            elif isinstance(value, dt.date):
                data[key] = value.isoformat()
            elif isinstance(value, Enum):
                data[key] = value.value
            elif isinstance(value, tuple):
                data[key] = list(value)
        return data

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        return f"{self.start_utc.isoformat()} {self.uid} {self.title}"


@dataclass(frozen=True)
class Dropped:
    """An event this module refused, with enough detail to argue about it."""

    reason: DropReason
    space_id: str
    source_label: str
    source_uid: str | None
    title: str | None
    start: str | None
    detail: str = ""

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        return f"{self.reason.value}: {self.title!r} @ {self.start} ({self.detail})"


@dataclass(frozen=True)
class TimezoneConversion:
    """One floating time interpreted by policy. Logged and reported, never mute."""

    space_id: str
    source_label: str
    source_uid: str | None
    title: str | None
    #: The wall-clock string the feed carried, with no zone attached.
    local: str
    #: The zone applied, and where it came from.
    zone: str
    origin: str
    utc: str

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        return (
            f"{self.space_id}:{self.source_label}: floating {self.local} read as "
            f"{self.zone} ({self.origin}) -> {self.utc}"
        )


@dataclass(frozen=True)
class Normalization:
    """The result: publishable events, quarantined ones, and what was dropped.

    Iterating or ``len()``-ing yields the **publishable** events. The other two
    collections exist so that "the feed shrank" always has an answer: nothing
    leaves this module without landing in one of the three.
    """

    events: tuple[Event, ...] = ()
    quarantined: tuple[Event, ...] = ()
    dropped: tuple[Dropped, ...] = ()
    conversions: tuple[TimezoneConversion, ...] = ()

    space_id: str = ""
    source_label: str = ""
    timezone: str = DEFAULT_TIMEZONE
    now: dt.datetime | None = None

    @property
    def event_count(self) -> int:
        return len(self.events)

    @property
    def input_count(self) -> int:
        return len(self.events) + len(self.quarantined) + len(self.dropped)

    def dropped_for(self, reason: DropReason) -> tuple[Dropped, ...]:
        return tuple(item for item in self.dropped if item.reason is reason)

    def quarantined_for(self, reason: QuarantineReason) -> tuple[Event, ...]:
        return tuple(item for item in self.quarantined if item.quarantine is reason)

    def __iter__(self) -> Iterator[Event]:
        return iter(self.events)

    def __len__(self) -> int:
        return len(self.events)

    def __getitem__(self, index: int) -> Event:
        return self.events[index]

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        return (
            f"{self.space_id}:{self.source_label} {len(self.events)} events, "
            f"{len(self.quarantined)} quarantined, {len(self.dropped)} dropped, "
            f"{len(self.conversions)} assumed zones"
        )


# --------------------------------------------------------------------------- time


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise UnknownTimezoneError(
            f"unknown timezone {name!r}: {exc}. A zone we cannot resolve is a "
            "configuration bug, not a reason to fall back to UTC."
        ) from exc


def is_aware(value: dt.datetime) -> bool:
    """True when *value* carries a usable offset."""
    return value.tzinfo is not None and value.tzinfo.utcoffset(value) is not None


def to_utc(
    value: dt.datetime,
    *,
    assume_tz: str | None = None,
    what: str = "datetime",
) -> dt.datetime:
    """Convert *value* to an aware UTC datetime, or raise.

    The enforcement point. A naive value raises :class:`NaiveDatetimeError`
    unless the caller states, explicitly and in code, which zone it should be
    read in — there is deliberately no default. Callers that pass ``assume_tz``
    are expected to log the fact; :func:`normalize_event` does.
    """
    if not isinstance(value, dt.datetime):
        raise NaiveDatetimeError(
            f"{what} is {type(value).__name__}, not a datetime. An all-day "
            "datetime.date is a distinct case — see normalize_event()."
        )
    if is_aware(value):
        return value.astimezone(UTC)
    if assume_tz is None:
        raise NaiveDatetimeError(
            f"naive {what} {value.isoformat()} reached normalize. Parse to an "
            "aware datetime at the adapter, or state the zone explicitly with "
            "assume_tz — never let this default to local time."
        )
    return value.replace(tzinfo=_zone(assume_tz)).astimezone(UTC)


def day_start_utc(day: dt.date, tz: str) -> dt.datetime:
    """Midnight at the start of *day* in *tz*, as an aware UTC instant.

    How an all-day :class:`datetime.date` becomes an instant: anchored, not
    coerced. ``date`` has no time and no zone, so pretending it is midnight UTC
    shifts every all-day event in this registry seven or eight hours and lands
    a fair number of them on the wrong day.
    """
    return dt.datetime.combine(day, dt.time.min, tzinfo=_zone(tz)).astimezone(UTC)


def _stamp(value: dt.datetime) -> str:
    """The UID's rendering of an instant: ``20260805T031500Z``.

    Second precision, no offset, no separators to vary. Anything that can
    change between two runs for the same occurrence would churn every
    subscriber's calendar, so it is deliberately coarse and deliberately UTC.
    """
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


# --------------------------------------------------------------------------- text

_PUNCT_RE = re.compile(r"[^\w\s]|_", re.UNICODE)
_SPACE_RE = re.compile(r"\s+", re.UNICODE)
_URL_RE = re.compile(r"^(https?://|www\.)\S*$", re.IGNORECASE)


def normalize_title(text: str | None) -> str:
    """Lowercase, strip punctuation, collapse whitespace.

    The ``normalize`` of ``sha1(space_id + start_utc + normalize(title))``, and
    also what dedupe compares on. Unicode is folded to NFKC first so that a
    curly apostrophe and a straight one do not produce two different UIDs for
    the same event on two different nights.
    """
    if not text:
        return ""
    folded = unicodedata.normalize("NFKC", text)
    stripped = _PUNCT_RE.sub(" ", folded)
    return _SPACE_RE.sub(" ", stripped).strip().lower()


def _clean(text: str | None) -> str | None:
    if text is None:
        return None
    value = text.strip()
    return value or None


def _is_bare_url(text: str) -> bool:
    """True for a ``LOCATION`` that is only a link.

    15% of Frontier Tower's events set ``LOCATION`` to a bare Luma URL. That is
    not a venue and must not be treated as one — but it is also not a reason to
    drop the event (see the filters invariant in CLAUDE.md).
    """
    return bool(_URL_RE.match(text.strip()))


# --------------------------------------------------------------------------- uids


def make_uid(
    space_id: str,
    *,
    source_uid: str | None = None,
    start_utc: dt.datetime | None = None,
    title: str | None = None,
    recurring: bool = False,
) -> str:
    """Build a stable UID. See the module docstring for the rule and the trap.

    Source-agnostic on purpose: an adapter passes whatever stable per-event key
    its source gives it — Maker Nexus's ``YYYY-MM-DD_<amiliaId>`` cache key,
    The Crucible's ``{id}:{start_epoch}``, Meetup's ``event_<id>@meetup.com`` —
    and gets the same namespacing treatment as an ICS ``UID``.

    ``recurring`` must come from the *unexpanded* source event. Setting it on a
    non-recurring event is not merely cosmetic: it puts the occurrence start
    into the UID, and any future change to how that start is computed would
    then churn the UID of an event that never needed one.
    """
    if not space_id:
        raise NormalizeError("space_id is required for a UID; it is the namespace")

    key = _clean(source_uid)
    if key is not None:
        if not recurring:
            return f"{space_id}:{key}"
        if start_utc is None:
            raise NormalizeError(
                f"{space_id}:{key} is recurring but has no start_utc. Every "
                "occurrence of a series shares one source UID, so the "
                "occurrence start is the only thing that separates them — "
                "without it the whole series collapses into one event."
            )
        return f"{space_id}:{key}:{_stamp(start_utc)}"

    if start_utc is None:
        raise NormalizeError(
            "an event with no source UID needs a start_utc to hash; without one "
            "there is nothing stable to build an identity from"
        )
    material = f"{space_id}{_stamp(start_utc)}{normalize_title(title)}"
    return hashlib.sha1(material.encode("utf-8")).hexdigest()[:_UID_HASH_CHARS]


def content_hash(
    *,
    title: str | None,
    start_utc: dt.datetime,
    end_utc: dt.datetime | None,
    location_name: str | None,
    address: str | None,
    url: str | None,
    description: str | None,
) -> str:
    """Fingerprint of the parts of an event a subscriber would notice changing.

    Excludes the UID, the run timestamps and everything a model wrote, so that
    re-running enrichment does not look like the source edited the event.
    """
    parts = [
        title or "",
        _stamp(start_utc),
        _stamp(end_utc) if end_utc else "",
        location_name or "",
        address or "",
        url or "",
        (description or "")[:2000],
    ]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- address


@dataclass(frozen=True)
class VenuePolicy:
    """What counts as "the space's own venue" for one source's event set.

    The address-override policy, in one object so it can be computed once per
    source and explained in one place.

    ``space.address_override`` exists because feeds ship stale or absent
    venues: Sudo Room puts the pre-2014 ``549 48th St`` on **every** event,
    Hacker Dojo's Meetup feed has no ``LOCATION`` at all, The Crucible's
    catalog has no location field of any kind. But Sudo Room's Luma feed also
    carries genuinely off-site events — one is at The Box Shop, 951 Hudson Ave
    in San Francisco — and an override that relocated those would publish
    confidently wrong addresses, which is worse than publishing none.

    Blank and URL-only locations are trivially safe to override. The hard case
    is a stale but *plausible* address, which no per-event heuristic can tell
    from a real off-site one: both are well-formed street addresses in the Bay
    Area. What separates them is a property of the **feed**, not the event —
    a stale house address is constant across the source, while off-site venues
    are the exception. So dominance is the test:

    - blank, or a bare URL                       -> override applies
    - names the space, or matches the override   -> override applies
    - held by a strict majority of this source's
      located events (the house venue)           -> override applies
    - anything else (a minority venue)           -> **kept**, ``off_site=True``

    Ties keep the source's location. When a two-event feed splits one-and-one
    there is no majority and no way to tell which is the house venue, and the
    failure directions are not symmetric: keeping a stale address is a wrong
    address on one event, while overriding is a wrong address on an event that
    was *right*, and it erases the evidence that it was.

    Nothing is erased in any case — :attr:`Event.location_name` always holds
    the source's ``LOCATION`` verbatim and :attr:`Event.address` holds the
    resolved postal address, so a bad call here is recoverable from the record.
    """

    override: str | None = None
    space_name: str = ""
    #: Normalized ``LOCATION`` values held by a strict majority of the source's
    #: located events.
    house_locations: frozenset[str] = frozenset()

    @classmethod
    def for_source(
        cls,
        space: Space,
        raws: Sequence[RawEvent],
    ) -> VenuePolicy:
        counts: dict[str, int] = {}
        located = 0
        for raw in raws:
            location = _clean(raw.location)
            if location is None or _is_bare_url(location):
                continue
            located += 1
            key = normalize_title(location)
            counts[key] = counts.get(key, 0) + 1

        house = frozenset(key for key, count in counts.items() if count * 2 > located)
        return cls(
            override=_clean(space.address_override),
            space_name=space.name or "",
            house_locations=house,
        )

    def resolve(self, location: str | None) -> tuple[str | None, AddressSource, bool]:
        """Return ``(address, source, off_site)`` for one event's ``LOCATION``."""
        text = _clean(location)

        if self.override is None:
            if text is None or _is_bare_url(text):
                return None, AddressSource.NONE, False
            return text, AddressSource.SOURCE, False

        if text is None or _is_bare_url(text):
            return self.override, AddressSource.OVERRIDE, False

        key = normalize_title(text)
        if (
            key in self.house_locations
            or key == normalize_title(self.override)
            or (self.space_name and normalize_title(self.space_name) in key)
        ):
            return self.override, AddressSource.OVERRIDE, False

        # A minority venue. The space said "my events are at X"; this event
        # says "not this one". Believe the event and flag it.
        return text, AddressSource.SOURCE, True


# --------------------------------------------------------------------------- core


def _space_timezone(space: Space, fallback: str | None = None) -> str:
    """The zone this space's wall-clock times are in.

    ``defaults.timezone`` from the registry when the space was built through
    :func:`~pipeline.config.load_registry`, otherwise the caller's fallback,
    otherwise :data:`DEFAULT_TIMEZONE`. Every space in this registry is in the
    Bay Area, but reading it from configuration keeps that a fact about the
    data rather than a fact about the code.
    """
    defaults = getattr(space, "_defaults", None)
    return getattr(defaults, "timezone", None) or fallback or DEFAULT_TIMEZONE


def normalize_event(
    raw: RawEvent,
    *,
    space: Space,
    source_label: str = "",
    timezone: str | None = None,
    calendar_timezone: str | None = None,
    now: dt.datetime | None = None,
    venue: VenuePolicy | None = None,
    allow_floating: bool = True,
    conversions: list[TimezoneConversion] | None = None,
) -> Event:
    """Normalize one :class:`RawEvent` into a canonical :class:`Event`.

    Raises :class:`NaiveDatetimeError` when a naive datetime arrives that the
    floating-time policy does not cover, and :class:`NormalizeError` when there
    is no start at all. Sanity drops and quarantine are the *caller's* decision
    — :func:`normalize_events` makes them — because a dropped event still has
    to be reported, and reporting it needs the normalized record.
    """
    now = now or dt.datetime.now(UTC)
    zone = timezone or _space_timezone(space)
    venue = venue if venue is not None else VenuePolicy.for_source(space, [raw])
    label = source_label or ""

    if raw.start is None:
        raise NormalizeError(
            f"{space.id}:{label}: event {raw.source_uid!r} has no start"
        )

    start_date: dt.date | None = None
    end_date: dt.date | None = None
    tz_assumed = False

    if raw.all_day or (
        isinstance(raw.start, dt.date) and not isinstance(raw.start, dt.datetime)
    ):
        # -- all-day: a date, anchored, never coerced --------------------------
        if isinstance(raw.start, dt.datetime):
            raise NormalizeError(
                f"{space.id}:{label}: {raw.source_uid!r} is flagged all_day but "
                "carries a datetime. The two forms are handled differently; "
                "guessing which one the adapter meant is how days go missing."
            )
        start_date = raw.start
        end_date = raw.end if isinstance(raw.end, dt.date) else None
        if isinstance(end_date, dt.datetime):  # pragma: no cover - defensive
            end_date = end_date.date()
        if end_date is None or end_date < start_date:
            end_date = start_date

        # The zone is the source's if it declared one, otherwise the space's.
        # An all-day date has no zone by definition, so this is a stated policy
        # rather than an assumption about a value that should have carried one.
        anchor_tz = raw.source_tz or zone
        tz_assumed = raw.source_tz is None
        start_utc = day_start_utc(start_date, anchor_tz)
        # Exclusive instant end: midnight starting the day after the inclusive
        # last day, which is what a timed consumer expects.
        end_utc = day_start_utc(end_date + dt.timedelta(days=1), anchor_tz)
        tz_name = anchor_tz
        LOG.debug(
            "%s:%s all-day %s anchored to midnight %s -> %s",
            space.id,
            label,
            start_date.isoformat(),
            anchor_tz,
            start_utc.isoformat(),
        )
    else:
        start_value = raw.start
        if not isinstance(start_value, dt.datetime):  # pragma: no cover - defensive
            raise NormalizeError(f"{space.id}:{label}: start is not a datetime")

        if is_aware(start_value):
            start_utc = to_utc(start_value, what="start")
            tz_name = raw.tz or raw.source_tz or _tzname(start_value) or "UTC"
        else:
            # -- naive. Floating, or a contradiction? --------------------------
            declares_zone = raw.source_tz is not None or raw.dtstart_form in (
                "utc",
                "tzid",
            )
            if declares_zone:
                raise NaiveDatetimeError(
                    f"{space.id}:{label}: {raw.source_uid!r} carries a naive "
                    f"start {start_value.isoformat()} while declaring "
                    f"source_tz={raw.source_tz!r} / dtstart_form="
                    f"{raw.dtstart_form!r}. Those cannot both be true, and "
                    "picking one is exactly the guess this invariant forbids."
                )
            if not allow_floating:
                raise NaiveDatetimeError(
                    f"{space.id}:{label}: {raw.source_uid!r} carries a floating "
                    f"start {start_value.isoformat()} and allow_floating is off."
                )
            tz_name, origin = (
                (calendar_timezone, "X-WR-TIMEZONE")
                if calendar_timezone
                else (zone, "registry timezone")
            )
            start_utc = to_utc(start_value, assume_tz=tz_name, what="floating start")
            tz_assumed = True
            note = TimezoneConversion(
                space_id=space.id,
                source_label=label,
                source_uid=raw.source_uid,
                title=raw.title,
                local=start_value.isoformat(),
                zone=tz_name,
                origin=origin,
                utc=start_utc.isoformat(),
            )
            LOG.warning(
                "%s:%s floating time %s for %r read as %s (%s) -> %s; the feed "
                "declared no TZID and no X-WR-TIMEZONE",
                space.id,
                label,
                note.local,
                raw.title,
                note.zone,
                note.origin,
                note.utc,
            )
            if conversions is not None:
                conversions.append(note)

        end_value = raw.end
        if isinstance(end_value, dt.datetime):
            end_utc = to_utc(
                end_value,
                assume_tz=None if is_aware(end_value) else tz_name,
                what="end",
            )
        else:
            end_utc = None

    address, address_source, off_site = venue.resolve(raw.location)
    title = _clean(raw.title) or ""

    uid = make_uid(
        space.id,
        source_uid=raw.source_uid,
        start_utc=start_utc,
        title=title,
        recurring=raw.recurring,
    )

    location_name = _clean(raw.location)
    description = _clean(raw.description)

    return Event(
        uid=uid,
        space_id=space.id,
        source_label=label,
        title=title,
        start_utc=start_utc,
        end_utc=end_utc,
        tz=tz_name,
        all_day=bool(raw.all_day),
        location_name=location_name,
        address=address,
        url=_clean(raw.url),
        price=_clean(raw.price),
        description=description,
        categories=tuple(raw.categories),
        summary_line=None,
        rrule=_clean(raw.rrule),
        first_seen=now,
        last_seen=now,
        content_hash=content_hash(
            title=title,
            start_utc=start_utc,
            end_utc=end_utc,
            location_name=location_name,
            address=address,
            url=_clean(raw.url),
            description=description,
        ),
        source_uid=_clean(raw.source_uid),
        dtstart_form=raw.dtstart_form,
        tz_assumed=tz_assumed,
        recurring=bool(raw.recurring),
        multi_day=bool(raw.multi_day),
        days=max(int(raw.days or 1), 1),
        start_date=start_date,
        end_date=end_date,
        address_source=address_source,
        off_site=off_site,
        quarantine=None,
    )


def _tzname(value: dt.datetime) -> str | None:
    tzinfo = value.tzinfo
    if tzinfo is None:  # pragma: no cover - guarded by callers
        return None
    return getattr(tzinfo, "key", None) or str(tzinfo)


def _quarantine_reason(event: Event) -> QuarantineReason | None:
    """Two shapes that mean the extraction went wrong, not the event.

    Both are withheld rather than dropped: an empty title is usually a parse
    bug worth seeing, and a 200+ character title is the classic signature of a
    model that swallowed the description into the ``title`` field. Dropping
    either silently would hide the very failure it signals.
    """
    if not event.title:
        return QuarantineReason.EMPTY_TITLE
    if len(event.title) > MAX_TITLE_CHARS:
        return QuarantineReason.TITLE_TOO_LONG
    return None


def _drop_reason(
    event: Event, *, now: dt.datetime, timezone: str
) -> tuple[DropReason, str] | None:
    """Sanity window: nothing in the past, nothing more than two years out.

    "In the past" is measured against the start of *today* in the source's
    zone, not against ``now``. The nightly run starts at 03:15 and an event at
    10:00 this morning is still an event; comparing against the wall clock
    would delete today's calendar every night for anyone reading it at noon.
    """
    floor = day_start_utc(now.astimezone(_zone(timezone)).date(), timezone)
    ceiling = now + dt.timedelta(days=MAX_FUTURE_DAYS)

    if event.all_day and event.end_date is not None:
        # An all-day event is current for the whole of its last day.
        last = day_start_utc(event.end_date + dt.timedelta(days=1), event.tz)
        if last <= floor:
            return DropReason.PAST, f"ended {event.end_date.isoformat()}"
    elif event.start_utc < floor:
        return DropReason.PAST, f"started {event.start_utc.isoformat()}"

    if event.start_utc > ceiling:
        return (
            DropReason.TOO_FAR,
            f"starts {event.start_utc.isoformat()}, beyond {MAX_FUTURE_DAYS} days",
        )
    return None


def normalize_events(
    raws: Iterable[RawEvent],
    *,
    space: Space,
    source_label: str = "",
    timezone: str | None = None,
    calendar_timezone: str | None = None,
    now: dt.datetime | None = None,
    allow_floating: bool = True,
) -> Normalization:
    """Normalize one source's events. The entry point every adapter uses.

    Batch rather than per-event because the address-override policy is a
    property of the feed: telling a stale house address from a genuine off-site
    venue needs to know how often each location appears. See
    :class:`VenuePolicy`.

    Order of judgement is drops first, then quarantine. A three-year-out event
    with a mangled title is out of the window regardless of its title, and
    quarantining it would fill the inspection queue with events nobody will
    ever publish.
    """
    now = now or dt.datetime.now(UTC)
    if not is_aware(now):
        raise NaiveDatetimeError(f"now is naive: {now.isoformat()}")
    zone = timezone or _space_timezone(space)
    _zone(zone)  # fail here rather than per-event

    items = list(raws)
    venue = VenuePolicy.for_source(space, items)
    conversions: list[TimezoneConversion] = []

    events: list[Event] = []
    quarantined: list[Event] = []
    dropped: list[Dropped] = []

    for raw in items:
        if raw.start is None:
            dropped.append(
                Dropped(
                    reason=DropReason.NO_START,
                    space_id=space.id,
                    source_label=source_label,
                    source_uid=raw.source_uid,
                    title=raw.title,
                    start=None,
                    detail="the source supplied no start",
                )
            )
            LOG.warning(
                "%s:%s dropped %r: no start", space.id, source_label, raw.title
            )
            continue

        event = normalize_event(
            raw,
            space=space,
            source_label=source_label,
            timezone=zone,
            calendar_timezone=calendar_timezone,
            now=now,
            venue=venue,
            allow_floating=allow_floating,
            conversions=conversions,
        )

        drop = _drop_reason(event, now=now, timezone=zone)
        if drop is not None:
            reason, detail = drop
            dropped.append(
                Dropped(
                    reason=reason,
                    space_id=space.id,
                    source_label=source_label,
                    source_uid=event.source_uid,
                    title=event.title,
                    start=event.start_utc.isoformat(),
                    detail=detail,
                )
            )
            LOG.info(
                "%s:%s dropped %r: %s (%s)",
                space.id,
                source_label,
                event.title,
                reason.value,
                detail,
            )
            continue

        held_for = _quarantine_reason(event)
        if held_for is not None:
            quarantined.append(replace(event, quarantine=held_for))
            LOG.warning(
                "%s:%s quarantined %s: %s (title %d chars)",
                space.id,
                source_label,
                event.uid,
                held_for.value,
                len(event.title),
            )
            continue

        events.append(event)

    events.sort(key=lambda item: (item.start_utc, item.uid))
    return Normalization(
        events=tuple(events),
        quarantined=tuple(quarantined),
        dropped=tuple(dropped),
        conversions=tuple(conversions),
        space_id=space.id,
        source_label=source_label,
        timezone=zone,
        now=now,
    )


def normalize_ics(
    parse: IcsParse,
    *,
    space: Space,
    source_label: str | None = None,
    timezone: str | None = None,
    now: dt.datetime | None = None,
    allow_floating: bool = True,
) -> Normalization:
    """Normalize an :class:`~pipeline.adapters.ics.IcsParse`.

    The ICS-shaped front door onto :func:`normalize_events`. The calendar's
    ``X-WR-TIMEZONE`` is passed through as the preferred zone for a floating
    time: a feed that declares a calendar-wide zone and then omits ``TZID`` on
    an event means the calendar's zone, and honoring that is closer to the
    source's intent than falling back to the registry default.

    A failed parse normalizes to an empty result rather than raising — a 200
    carrying ``text/html`` is the adapter's verdict to report, and issue 0014's
    carry-forward, not something to crash the run over here.
    """
    if not parse.ok:
        LOG.warning(
            "%s:%s not normalized: %s",
            parse.space_id or space.id,
            parse.label,
            parse.error,
        )
        return Normalization(
            space_id=space.id,
            source_label=source_label or parse.label,
            timezone=timezone or _space_timezone(space),
            now=now,
        )

    return normalize_events(
        (from_ics_event(event) for event in parse.events),
        space=space,
        source_label=source_label or parse.label,
        timezone=timezone,
        calendar_timezone=parse.calendar_timezone,
        now=now,
        allow_floating=allow_floating,
    )


__all__ = [
    "DEFAULT_TIMEZONE",
    "MAX_FUTURE_DAYS",
    "MAX_TITLE_CHARS",
    "AddressSource",
    "Dropped",
    "DropReason",
    "Event",
    "NaiveDatetimeError",
    "NormalizeError",
    "Normalization",
    "QuarantineReason",
    "RawEvent",
    "TimezoneConversion",
    "UnknownTimezoneError",
    "VenuePolicy",
    "content_hash",
    "day_start_utc",
    "from_ics_event",
    "is_aware",
    "make_uid",
    "normalize_event",
    "normalize_events",
    "normalize_ics",
    "normalize_title",
    "to_utc",
]
