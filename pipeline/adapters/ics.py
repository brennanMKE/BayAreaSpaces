"""iCalendar adapter: parse, expand recurrences, clip to the horizon.

The highest-value adapter in the project. ``ics`` alone covers Ace, Sudo Room,
Noisebridge, Frontier (×2), Humanmade (×2), The Box Shop and Hacker Dojo, and
``gcal_ics`` (issue 0008) is a thin wrapper that builds a URL and delegates here.
This module is the reason CLAUDE.md chose Python.

What this module is responsible for
-----------------------------------

- Parse with ``icalendar``; expand recurrences with ``recurring-ical-events``
  over ``today → today + horizon_days``.
- **Return post-expansion, horizon-clipped events.** Raw VEVENT counts are close
  to meaningless in this registry — Sequoia Fabrica has 89 VEVENTs and ~7 live,
  Maker Nexus 3645 and 171, Sudo Room 5057 (pre-expanded to 2058) and 73 in
  horizon. Both counts are reported, separately and unambiguously, because the
  health gates (issue 0016) must count the second one.
- Handle every ``DTSTART`` form that appears in these feeds: ``VALUE=DATE``
  all-day, floating local times with no ``TZID``, bare-UTC ``Z``, and ``TZID=``
  — the last two **within the same feed**, which Sequoia Fabrica's calendar
  genuinely does.
- Normalize Luma's multi-day-as-all-day encoding, which otherwise looks wrong
  once merged with timed events from other spaces.
- Preserve the source ``UID`` verbatim so ``normalize.py`` (issue 0009) can
  namespace it as ``{space_id}:{source_uid}``.
- Surface ``LAST-MODIFIED`` / ``DTSTAMP`` age, which is what issue 0016's
  ``max_stale_days`` gate runs on.

What this module deliberately does **not** do
---------------------------------------------

No timezone normalization, no UID namespacing, no filtering, no address
overrides. That is ``normalize.py``'s job (issues 0009/0010) and the seam is
kept clean: this module reports what the feed said, in the feed's own terms,
plus enough provenance (:attr:`IcsEvent.dtstart_form`, :attr:`IcsEvent.source_tz`)
for the next stage to do its work without re-reading the ICS.

Unbounded RRULEs are a live hazard, not a hypothetical
------------------------------------------------------

Noisebridge's retired "Noisebridge Daily" gCal has five RRULEs with **no
``UNTIL``** and has been dead since 2024-01. It fabricates roughly five
plausible events a week, forever, at a constant rate — a feed that is not
silently empty but silently *inventing*. Expansion here is always bounded by
the horizon window so that cannot explode; catching the underlying rot is
``max_stale_days`` on :attr:`IcsParse.last_change`, not this module's job.

"HTTP 200 is not success"
-------------------------

A 200 carrying ``text/html`` is a **failure to report clearly**, not a crash and
not a silent empty list. :func:`parse_ics` never raises for a bad payload: it
returns an :class:`IcsParse` with ``ok=False``, a :class:`IcsProblem` and a
human-readable ``error``. The content type is asserted through
``FetchResult.expect_content_type`` / ``content_type_is``, and the body is
sniffed for ``BEGIN:VCALENDAR`` as well — because in this registry ``?ical=1``
has been observed returning 200 with a byte-identical copy of the homepage.

Implemented by issue 0007.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Any

import icalendar
import recurring_ical_events

from pipeline.fetch import ContentTypeMismatch, FetchResult, Outcome

# --------------------------------------------------------------------------- knobs

#: Default horizon, matching ``defaults.horizon_days`` in ``sources.yaml``. The
#: caller should pass the registry's value; this is only the fallback.
DEFAULT_HORIZON_DAYS = 120

#: Content types a calendar feed may honestly declare.
CALENDAR_CONTENT_TYPES: tuple[str, ...] = (
    "text/calendar",
    "text/x-vcalendar",
    "application/calendar",
    "application/ics",
    "text/ics",
)

#: Types we tolerate *only* when the body actually sniffs as a VCALENDAR. Some
#: hosts serve a perfectly good ``.ics`` as ``text/plain`` or as an opaque
#: download; that is sloppy, not dishonest. ``text/html`` is never in here.
LENIENT_CONTENT_TYPES: tuple[str, ...] = (
    "text/plain",
    "application/octet-stream",
)

#: The marker every iCalendar body must open with (after any BOM/whitespace).
CALENDAR_MAGIC = "BEGIN:VCALENDAR"

#: How far into the body to look for :data:`CALENDAR_MAGIC`. A real feed has it
#: in the first line; scanning a whole 8 MB Sudo Room export would be silly.
_SNIFF_BYTES = 4096


# --------------------------------------------------------------------------- outcomes


class IcsProblem(str, Enum):
    """Why an :class:`IcsParse` is not usable. ``NONE`` means it is.

    Kept as an enum rather than a bare message so issue 0016's health gates and
    issue 0014's carry-forward can branch on the *kind* of failure. A
    ``WRONG_CONTENT_TYPE`` is a source that has drifted and needs a human; a
    ``TRANSPORT`` failure is a 503 that must carry forward yesterday's events.
    """

    NONE = "none"
    TRANSPORT = "transport"
    NOT_MODIFIED = "not_modified"
    EMPTY_BODY = "empty_body"
    WRONG_CONTENT_TYPE = "wrong_content_type"
    NOT_CALENDAR = "not_calendar"
    UNPARSEABLE = "unparseable"


#: How a source VEVENT wrote its ``DTSTART``. All four occur in this registry,
#: and ``UTC`` and ``TZID`` occur in the *same* feed (Sequoia Fabrica).
DtStartForm = str  # "date" | "utc" | "tzid" | "floating" | "unknown"


# --------------------------------------------------------------------------- events


@dataclass(frozen=True)
class IcsEvent:
    """One post-expansion, horizon-clipped occurrence.

    Intermediate shape: the fields the ICS actually carried, plus the provenance
    ``normalize.py`` needs. **Not** the canonical event — no UID namespacing and
    no timezone normalization has happened yet.
    """

    #: The source ``UID``, verbatim. Issue 0009 namespaces it as
    #: ``{space_id}:{source_uid}``; it must survive this stage untouched or every
    #: subscriber sees every event as new every night.
    uid: str | None

    title: str | None

    #: ``datetime.date`` when :attr:`all_day`, otherwise ``datetime.datetime``.
    #: The datetime is aware unless the feed used a floating time *and* declared
    #: no ``X-WR-TIMEZONE`` — see :attr:`dtstart_form`. Issue 0009 is where a
    #: naive value becomes a hard error.
    start: dt.date | dt.datetime

    #: For all-day events this is the **inclusive** last day, not the exclusive
    #: ``DTEND`` the ICS carried — see :func:`_normalize_all_day`.
    end: dt.date | dt.datetime | None

    all_day: bool = False

    #: True when the event covers more than one calendar day. For an all-day
    #: event this is Luma's lossy multi-day encoding; see the module docstring.
    multi_day: bool = False

    #: Number of calendar days covered, inclusive. 1 for an ordinary event.
    days: int = 1

    #: IANA name of the zone attached to :attr:`start` after expansion, or
    #: ``None`` if the value is naive or a bare date.
    tz: str | None = None

    #: What the *source* ``DTSTART`` declared, before ``recurring-ical-events``
    #: applied any ``X-WR-TIMEZONE`` correction: the ``TZID`` value, ``"UTC"``
    #: for the bare-``Z`` form, or ``None`` for floating and all-day.
    source_tz: str | None = None

    #: ``"date"`` | ``"utc"`` | ``"tzid"`` | ``"floating"``.
    dtstart_form: DtStartForm = "unknown"

    location: str | None = None
    description: str | None = None
    url: str | None = None
    categories: tuple[str, ...] = ()

    status: str | None = None
    organizer: str | None = None
    sequence: int | None = None

    #: Instance identity within a recurring series. ``recurring-ical-events``
    #: sets ``RECURRENCE-ID`` on every occurrence it emits, so this is the only
    #: thing that distinguishes the 17 occurrences that share one ``UID``.
    recurrence_id: dt.date | dt.datetime | None = None

    #: True when the source VEVENT carried an ``RRULE`` / ``RDATE``. Sudo Room's
    #: export has none — it pre-expands to 2058 instead.
    recurring: bool = False

    last_modified: dt.datetime | None = None
    dtstamp: dt.datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        """A plain dict, for callers that would rather not import the class."""
        return asdict(self)

    @property
    def dtend_exclusive(self) -> dt.date | dt.datetime | None:
        """The ``DTEND`` as the ICS wrote it — exclusive for all-day events."""
        if self.all_day and isinstance(self.end, dt.date):
            return self.end + dt.timedelta(days=1)
        return self.end

    @property
    def instance_key(self) -> str:
        """``uid`` plus the occurrence start — unique within one feed.

        Offered because ``UID`` alone is *not* unique after expansion: one
        Noisebridge RRULE yields 17 occurrences sharing a single UID, and
        namespacing that to ``{space_id}:{source_uid}`` 17 times would collapse
        them. Issue 0009 decides what to do about that; this is the material.
        """
        stamp = self.recurrence_id if self.recurrence_id is not None else self.start
        return f"{self.uid or ''}#{_isoformat(stamp)}"

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        return f"{_isoformat(self.start)} {self.title or '<untitled>'}"


# --------------------------------------------------------------------------- result


@dataclass(frozen=True)
class IcsParse:
    """The adapter's return value: events, feed metadata and a clear verdict.

    Iterating or ``len()``-ing this yields the events, so a caller that only
    wants the list can treat it as one. The wrapper exists because two things
    genuinely cannot be expressed by a bare list: a *reported* failure (a 200
    with ``text/html`` must not be an empty list), and the feed-level
    ``LAST-MODIFIED`` age that issue 0016's ``max_stale_days`` gate runs on.
    """

    events: tuple[IcsEvent, ...] = ()

    problem: IcsProblem = IcsProblem.NONE
    error: str | None = None

    # -- provenance ------------------------------------------------------------
    space_id: str = ""
    label: str = ""
    source_url: str = ""

    # -- feed metadata (issue 0008 surfaces these for gcal_ics) -----------------
    prodid: str | None = None
    calendar_name: str | None = None  # X-WR-CALNAME
    calendar_description: str | None = None  # X-WR-CALDESC
    calendar_timezone: str | None = None  # X-WR-TIMEZONE
    refresh_interval: str | None = None  # REFRESH-INTERVAL / X-PUBLISHED-TTL

    # -- counts, kept apart on purpose -----------------------------------------
    #: VEVENTs in the file, before expansion and before clipping. Sudo Room:
    #: 5057. Almost meaningless as a health signal; here for the run report.
    vevent_count: int = 0
    #: VEVENTs carrying an RRULE/RDATE. Noisebridge's dead gCal: 28 VEVENTs of
    #: which 5 recur without an ``UNTIL``.
    recurring_vevent_count: int = 0
    # -- staleness (issue 0016) -------------------------------------------------
    #: Newest ``LAST-MODIFIED`` anywhere in the file, including VEVENTs **outside**
    #: the horizon. Scanning only the expanded window would miss exactly the case
    #: this exists for: Noisebridge's newest LAST-MODIFIED is 2024-06-13 while
    #: its unbounded RRULEs keep generating events dated next week.
    last_modified: dt.datetime | None = None
    #: Newest ``DTSTAMP`` anywhere in the file. Fallback when the producer omits
    #: ``LAST-MODIFIED`` — Meetup's iCal does.
    dtstamp: dt.datetime | None = None

    # -- window ----------------------------------------------------------------
    horizon_days: int = DEFAULT_HORIZON_DAYS
    window_start: dt.date | None = None
    window_end: dt.date | None = None
    parsed_at: dt.datetime | None = None

    # -- verdict ---------------------------------------------------------------

    @property
    def ok(self) -> bool:
        """True when the payload was a calendar and we parsed it.

        Zero events with ``ok=True`` is a legitimate state — The Box Shop runs
        8–12 events a year and is routinely empty. That is ``allow_zero``'s
        problem, not a parse failure.
        """
        return self.problem is IcsProblem.NONE

    @property
    def event_count(self) -> int:
        """Post-expansion events inside the horizon. **This** is what to count."""
        return len(self.events)

    @property
    def last_change(self) -> dt.datetime | None:
        """Best available "when did this feed last change": ``LAST-MODIFIED``,
        falling back to ``DTSTAMP``. The input to ``max_stale_days``."""
        return self.last_modified or self.dtstamp

    @property
    def stale_days(self) -> float | None:
        """Age of :attr:`last_change` in days at parse time, or ``None``.

        ``None`` means the feed dated nothing at all, which is itself worth
        reporting — it is not the same as "fresh".
        """
        change = self.last_change
        if change is None or self.parsed_at is None:
            return None
        return (self.parsed_at - change).total_seconds() / 86400.0

    # -- list-ish conveniences --------------------------------------------------

    def __iter__(self) -> Iterator[IcsEvent]:
        return iter(self.events)

    def __len__(self) -> int:
        return len(self.events)

    def __getitem__(self, index: int) -> IcsEvent:
        return self.events[index]

    def __bool__(self) -> bool:
        # Deliberately the verdict, not the count: an empty-but-valid feed is
        # truthy, and a text/html body is falsy even though both hold 0 events.
        return self.ok

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        head = f"{self.space_id}:{self.label}" if self.space_id or self.label else "<ics>"
        if not self.ok:
            return f"{head} FAILED {self.problem.value}: {self.error}"
        return (
            f"{head} {self.event_count} events in {self.horizon_days}d "
            f"(from {self.vevent_count} VEVENTs)"
        )


# --------------------------------------------------------------------------- helpers


def looks_like_calendar(text: str) -> bool:
    """True if the body opens with ``BEGIN:VCALENDAR``.

    The second half of "HTTP 200 is not success": in this registry ``?ical=1``
    has been observed answering 200 with a byte-identical copy of the homepage.
    A content-type check alone would wave that through.
    """
    return CALENDAR_MAGIC in text[:_SNIFF_BYTES].lstrip("﻿ \t\r\n").upper()


def _text(component: Any, name: str) -> str | None:
    value = component.get(name)
    if value is None:
        return None
    if isinstance(value, list):
        value = value[0] if value else None
        if value is None:
            return None
    text = str(value).strip()
    return text or None


def _ical_text(component: Any, name: str) -> str | None:
    """A property re-serialized to its iCalendar form.

    Used for typed properties whose Python value does not round-trip through
    ``str()``: ``REFRESH-INTERVAL`` parses to a ``timedelta``, and
    ``str(timedelta)`` is ``"12:00:00"`` rather than ``"PT12H"``.
    """
    value = component.get(name)
    if value is None:
        return None
    encode = getattr(value, "to_ical", None)
    if encode is None:
        return _text(component, name)
    encoded = encode()
    if isinstance(encoded, bytes):
        encoded = encoded.decode("utf-8", errors="replace")
    encoded = str(encoded).strip()
    return encoded or None


def _organizer(component: Any) -> str | None:
    """``ORGANIZER``, preferring the human-readable ``CN`` parameter.

    Frontier Tower's ICS is where this earns its place: the mailto addresses are
    anonymous Luma relays while ``CN=`` carries the actual organizer names.
    """
    prop = component.get("ORGANIZER")
    if prop is None:
        return None
    if isinstance(prop, list):
        prop = prop[0]
    common_name = (getattr(prop, "params", {}) or {}).get("CN")
    if common_name:
        name = str(common_name).strip().strip('"')
        if name:
            return name
    value = str(prop).strip()
    return value or None


def _int(component: Any, name: str) -> int | None:
    value = component.get(name)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return None


def _categories(component: Any) -> tuple[str, ...]:
    """Flatten ``CATEGORIES``, which may repeat and may hold a list per line."""
    raw = component.get("CATEGORIES")
    if raw is None:
        return ()
    entries = raw if isinstance(raw, list) else [raw]
    out: list[str] = []
    for entry in entries:
        cats = getattr(entry, "cats", None)
        values: Iterable[Any] = cats if cats is not None else str(entry).split(",")
        for value in values:
            name = str(value).strip()
            if name and name not in out:
                out.append(name)
    return tuple(out)


def _datetime_prop(component: Any, name: str) -> dt.datetime | None:
    """A ``LAST-MODIFIED``/``DTSTAMP``-style property as an aware UTC datetime."""
    prop = component.get(name)
    value = getattr(prop, "dt", None)
    if not isinstance(value, dt.datetime):
        return None
    if value.tzinfo is None:
        # Both properties are defined as UTC by RFC 5545; a producer that drops
        # the Z is being sloppy, not ambiguous.
        return value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def _dtstart_form(component: Any) -> tuple[DtStartForm, str | None]:
    """Classify the *source* ``DTSTART``: form, and the tz it named.

    Read from the original VEVENT rather than from the expanded occurrence,
    because ``recurring-ical-events`` applies the calendar's ``X-WR-TIMEZONE``
    and will hand back a bare-UTC ``Z`` event already converted to local time.
    That conversion preserves the instant but erases the evidence of which form
    the feed used, and issue 0009 is asked to "carry the original tz string".
    """
    prop = component.get("DTSTART")
    if prop is None:
        return "unknown", None
    params = getattr(prop, "params", {}) or {}
    tzid = params.get("TZID")
    if tzid:
        return "tzid", str(tzid)
    value = getattr(prop, "dt", None)
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            return "floating", None
        return "utc", "UTC"
    if isinstance(value, dt.date):
        return "date", None
    return "unknown", None


def _tz_name(value: Any) -> str | None:
    tzinfo = getattr(value, "tzinfo", None)
    if tzinfo is None:
        return None
    return getattr(tzinfo, "key", None) or str(tzinfo)


def _isoformat(value: Any) -> str:
    try:
        return value.isoformat()
    except AttributeError:  # pragma: no cover - defensive
        return str(value)


def _normalize_all_day(
    start: dt.date, end: dt.date | None
) -> tuple[dt.date, int]:
    """Turn an ICS all-day ``DTEND`` into an **inclusive** last day and a count.

    This is the Luma normalization. RFC 5545 makes ``DTEND`` exclusive for
    ``VALUE=DATE``, so a one-day event ends "tomorrow" and a three-day event
    ends on the fourth day. Luma encodes *multi-day* events — which have real
    start and end times it simply does not export — as all-day spans, and if the
    exclusive end is carried through unchanged they render a day longer than
    they are and sort above every timed event on a day nothing happens.

    We cannot recover the times Luma dropped, and inventing them would be worse
    than the problem. What we can do is make the span honest and label it: an
    inclusive end, a day count, and :attr:`IcsEvent.multi_day` so ``emit`` can
    render one banner instead of phantom instances.
    """
    if end is None or end <= start:
        return start, 1
    inclusive_end = end - dt.timedelta(days=1)
    days = (inclusive_end - start).days + 1
    return inclusive_end, max(days, 1)


def _sort_key(event: IcsEvent) -> tuple[dt.date, int, dt.time, str]:
    """Order by wall-clock day, all-day first, then time. Mixed date/datetime
    values cannot be compared directly, and this feed set contains both."""
    start = event.start
    if isinstance(start, dt.datetime):
        moment = start.astimezone(dt.timezone.utc) if start.tzinfo else start
        return (moment.date(), 1, moment.time(), event.uid or "")
    return (start, 0, dt.time.min, event.uid or "")


# --------------------------------------------------------------------------- parsing


def _failure(
    problem: IcsProblem,
    error: str,
    *,
    space_id: str = "",
    label: str = "",
    source_url: str = "",
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    window_start: dt.date | None = None,
    window_end: dt.date | None = None,
    parsed_at: dt.datetime | None = None,
) -> IcsParse:
    return IcsParse(
        problem=problem,
        error=error,
        space_id=space_id,
        label=label,
        source_url=source_url,
        horizon_days=horizon_days,
        window_start=window_start,
        window_end=window_end,
        parsed_at=parsed_at,
    )


def parse_ics(
    result: FetchResult,
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    today: dt.date | None = None,
    now: dt.datetime | None = None,
    strict_content_type: bool = True,
) -> IcsParse:
    """Parse a fetched iCalendar payload into horizon-clipped events.

    ``result`` is issue 0006's :class:`~pipeline.fetch.FetchResult`, whose
    status, content type and body are deliberately kept apart. This function
    makes the judgement the fetch layer refused to make on its behalf — and
    reports it rather than raising.

    Never raises for a bad payload. A transport failure, a 304, an empty body, a
    ``text/html`` body and unparseable bytes all come back as an
    :class:`IcsParse` with ``ok=False`` and a :class:`IcsProblem`.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    today = today or now.date()
    window_start = today
    window_end = today + dt.timedelta(days=horizon_days)

    fail = lambda problem, error: _failure(  # noqa: E731 - keeps the guards readable
        problem,
        error,
        space_id=result.space_id,
        label=result.label,
        source_url=result.url,
        horizon_days=horizon_days,
        window_start=window_start,
        window_end=window_end,
        parsed_at=now,
    )

    # --- transport first: there may be nothing to judge -----------------------
    if result.outcome is Outcome.NOT_MODIFIED:
        return fail(
            IcsProblem.NOT_MODIFIED,
            f"{result.source_key}: HTTP 304, unchanged since the last run — reuse "
            "the stored events rather than treating this as zero",
        )
    if result.outcome is not Outcome.FETCHED:
        return fail(
            IcsProblem.TRANSPORT,
            f"{result.source_key}: {result.outcome.value}"
            + (f" ({result.reason})" if result.reason else ""),
        )
    if not result.has_body:
        return fail(
            IcsProblem.EMPTY_BODY,
            f"{result.source_key}: HTTP {result.status_code} with an empty body from "
            f"{result.url}",
        )

    text = result.text

    # --- content type, asserted but not blindly ------------------------------
    tolerated: str | None = None
    if strict_content_type:
        try:
            result.expect_content_type(*CALENDAR_CONTENT_TYPES, allow_missing=True)
        except ContentTypeMismatch as exc:
            if result.content_type_is(*LENIENT_CONTENT_TYPES) and looks_like_calendar(text):
                # Sloppy, not dishonest: a real VCALENDAR mislabelled by the host.
                tolerated = result.content_type
            else:
                return fail(
                    IcsProblem.WRONG_CONTENT_TYPE,
                    f"{exc} — HTTP 200 is not success; this is a source that has "
                    "drifted, not an empty calendar",
                )

    # --- and the body sniffed anyway ------------------------------------------
    if not looks_like_calendar(text):
        return fail(
            IcsProblem.NOT_CALENDAR,
            f"{result.source_key}: HTTP {result.status_code} declared "
            f"{result.content_type or '<no Content-Type>'} but the body carries no "
            f"{CALENDAR_MAGIC} ({result.byte_count} bytes, sha256 "
            f"{(result.content_hash or '')[:12]}) — the byte-identical-homepage case",
        )

    parse = parse_ics_text(
        text,
        space_id=result.space_id,
        label=result.label,
        source_url=result.url,
        horizon_days=horizon_days,
        today=today,
        now=now,
    )
    if tolerated and parse.ok:
        return replace(
            parse,
            error=f"content type {tolerated!r} tolerated: the body is a valid VCALENDAR",
        )
    return parse


def parse_ics_text(
    text: str,
    *,
    space_id: str = "",
    label: str = "",
    source_url: str = "",
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    today: dt.date | None = None,
    now: dt.datetime | None = None,
) -> IcsParse:
    """Parse iCalendar *text*. The half of :func:`parse_ics` with no HTTP in it.

    Split out so ``gcal_ics`` (issue 0008), the fixtures in ``tests/`` and a
    human diffing two files out of ``raw/`` can all reach the parser without
    manufacturing a :class:`~pipeline.fetch.FetchResult`.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    today = today or now.date()
    window_start = today
    window_end = today + dt.timedelta(days=horizon_days)

    if not looks_like_calendar(text):
        return _failure(
            IcsProblem.NOT_CALENDAR,
            f"{space_id}:{label}: body carries no {CALENDAR_MAGIC}",
            space_id=space_id,
            label=label,
            source_url=source_url,
            horizon_days=horizon_days,
            window_start=window_start,
            window_end=window_end,
            parsed_at=now,
        )

    try:
        calendar = icalendar.Calendar.from_ical(text)
    except Exception as exc:  # icalendar raises bare ValueError subclasses
        return _failure(
            IcsProblem.UNPARSEABLE,
            f"{space_id}:{label}: icalendar could not parse the body: "
            f"{type(exc).__name__}: {exc}",
            space_id=space_id,
            label=label,
            source_url=source_url,
            horizon_days=horizon_days,
            window_start=window_start,
            window_end=window_end,
            parsed_at=now,
        )

    # --- the source VEVENTs, scanned once -------------------------------------
    #
    # Everything here is read from the *unexpanded* components: the DTSTART form
    # (which expansion rewrites), the recurrence flag, and the staleness clock.
    # The staleness scan in particular must cover VEVENTs outside the horizon —
    # Noisebridge's whole file is dated 2024 while its output is dated next week.
    sources: dict[str, Any] = {}
    vevent_count = 0
    recurring_vevent_count = 0
    newest_modified: dt.datetime | None = None
    newest_stamp: dt.datetime | None = None

    for component in calendar.walk("VEVENT"):
        vevent_count += 1
        uid = _text(component, "UID")
        if uid is not None and uid not in sources:
            sources[uid] = component
        if component.get("RRULE") is not None or component.get("RDATE") is not None:
            recurring_vevent_count += 1
        modified = _datetime_prop(component, "LAST-MODIFIED")
        if modified is not None and (newest_modified is None or modified > newest_modified):
            newest_modified = modified
        stamp = _datetime_prop(component, "DTSTAMP")
        if stamp is not None and (newest_stamp is None or stamp > newest_stamp):
            newest_stamp = stamp

    calendar_modified = _datetime_prop(calendar, "LAST-MODIFIED")
    if calendar_modified is not None and (
        newest_modified is None or calendar_modified > newest_modified
    ):
        newest_modified = calendar_modified

    # --- expansion, always bounded by the window ------------------------------
    #
    # ``between`` is the bound. Nothing here ever materializes a series without
    # one, which is what keeps five UNTIL-less Noisebridge RRULEs to ~85 events
    # instead of an unbounded generator.
    try:
        query = recurring_ical_events.of(calendar, skip_bad_series=True)
        occurrences = query.between(window_start, window_end)
    except Exception as exc:
        return _failure(
            IcsProblem.UNPARSEABLE,
            f"{space_id}:{label}: recurrence expansion failed: "
            f"{type(exc).__name__}: {exc}",
            space_id=space_id,
            label=label,
            source_url=source_url,
            horizon_days=horizon_days,
            window_start=window_start,
            window_end=window_end,
            parsed_at=now,
        )

    events = tuple(
        sorted(
            (_build_event(occurrence, sources) for occurrence in occurrences),
            key=_sort_key,
        )
    )

    return IcsParse(
        events=events,
        problem=IcsProblem.NONE,
        space_id=space_id,
        label=label,
        source_url=source_url,
        prodid=_text(calendar, "PRODID"),
        calendar_name=_text(calendar, "X-WR-CALNAME"),
        calendar_description=_text(calendar, "X-WR-CALDESC"),
        calendar_timezone=_text(calendar, "X-WR-TIMEZONE"),
        refresh_interval=_ical_text(calendar, "REFRESH-INTERVAL")
        or _ical_text(calendar, "X-PUBLISHED-TTL"),
        vevent_count=vevent_count,
        recurring_vevent_count=recurring_vevent_count,
        last_modified=newest_modified,
        dtstamp=newest_stamp,
        horizon_days=horizon_days,
        window_start=window_start,
        window_end=window_end,
        parsed_at=now,
    )


def _build_event(occurrence: Any, sources: dict[str, Any]) -> IcsEvent:
    """Turn one expanded VEVENT into an :class:`IcsEvent`."""
    uid = _text(occurrence, "UID")

    # The DTSTART *form* comes from the original component. See _dtstart_form.
    source = sources.get(uid) if uid is not None else None
    form, source_tz = _dtstart_form(source if source is not None else occurrence)

    start_prop = occurrence.get("DTSTART")
    start = getattr(start_prop, "dt", None)
    end_prop = occurrence.get("DTEND")
    end = getattr(end_prop, "dt", None)

    all_day = isinstance(start, dt.date) and not isinstance(start, dt.datetime)
    days = 1
    multi_day = False

    if all_day:
        end, days = _normalize_all_day(start, end if isinstance(end, dt.date) else None)
        multi_day = days > 1
    elif isinstance(start, dt.datetime) and isinstance(end, dt.datetime):
        # A timed event can span days too (hackathons, festivals). Same flag,
        # different cause — and no exclusive-end correction, DTEND is a real
        # instant here.
        days = max((end.date() - start.date()).days + 1, 1)
        multi_day = days > 1

    recurrence_prop = occurrence.get("RECURRENCE-ID")
    recurrence_id = getattr(recurrence_prop, "dt", None)

    recurring = False
    if source is not None:
        recurring = source.get("RRULE") is not None or source.get("RDATE") is not None

    return IcsEvent(
        uid=uid,
        title=_text(occurrence, "SUMMARY"),
        start=start,
        end=end,
        all_day=all_day,
        multi_day=multi_day,
        days=days,
        tz=_tz_name(start),
        source_tz=source_tz,
        dtstart_form=form,
        location=_text(occurrence, "LOCATION"),
        description=_text(occurrence, "DESCRIPTION"),
        url=_text(occurrence, "URL"),
        categories=_categories(occurrence),
        status=_text(occurrence, "STATUS"),
        organizer=_organizer(occurrence),
        sequence=_int(occurrence, "SEQUENCE"),
        recurrence_id=recurrence_id,
        recurring=recurring,
        last_modified=_datetime_prop(occurrence, "LAST-MODIFIED"),
        dtstamp=_datetime_prop(occurrence, "DTSTAMP"),
    )


#: The adapter entry point named by ``sources.yaml``'s ``adapter: ics``.
parse = parse_ics


__all__ = [
    "CALENDAR_CONTENT_TYPES",
    "CALENDAR_MAGIC",
    "DEFAULT_HORIZON_DAYS",
    "LENIENT_CONTENT_TYPES",
    "IcsEvent",
    "IcsParse",
    "IcsProblem",
    "looks_like_calendar",
    "parse",
    "parse_ics",
    "parse_ics_text",
]
