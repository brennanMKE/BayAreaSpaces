"""Merged and per-space iCalendar output.

Produces the Phase 1 deliverable: the artifact that makes every later decision
easier to judge and the demo that makes outreach persuasive.

Responsibilities:

- One ``VCALENDAR`` with ``PRODID:-//brennan.sstools.co//maker-calendar//EN``,
  ``X-WR-CALNAME:Bay Area Makerspaces`` and
  ``X-WR-TIMEZONE:America/Los_Angeles``.
- **Emit expanded instances, not RRULEs.** Cross-client recurrence handling is
  where this goes wrong, and expansion is already in memory from ``ics``.
- Every ``VEVENT`` carries ``URL`` pointing at the space's own page and
  ``ORGANIZER`` naming the space — the point is to drive people to the spaces.
- Prefix ``SUMMARY`` with the space: ``[Ace] Sewing 101 Bootcamp``.
- ``DESCRIPTION`` truncated to roughly 300 characters plus a link.
- Also write per-space files under ``out/spaces/{space_id}.ics``.
- Validate the result before it replaces the live file.

Why no ``RRULE``, ever
----------------------

:attr:`~pipeline.normalize.Event.rrule` is carried on the record and is
deliberately **not** written out. The adapter has already expanded the series
against the horizon, so re-stating the rule would either duplicate every
occurrence or hand each client its own chance to disagree with us about what
``FREQ=MONTHLY;BYDAY=-1SU`` means in a DST transition. One expanded instance
per occurrence is a larger file and a smaller surface. :func:`validate_ics`
asserts the absence rather than trusting the builder, because a stray ``RRULE``
would look fine in the file and wrong in six calendar clients.

Which URL an event points at
----------------------------

Both are present on every ``VEVENT`` and they answer different questions.
``ORGANIZER`` always carries the space's own page and its name — that is the
"drive people to the spaces" half, and it is invariant. ``URL`` prefers the
event's own page when the source gave one (that is the deep link a subscriber
actually wants, and Part 5's "link back to the source page on every event"),
falling back to the space page so ``URL`` is never absent. Pass
``prefer_event_url=False`` to make ``URL`` the space page unconditionally.

All-day is a different shape, not a different time
--------------------------------------------------

Issue 0009 keeps :attr:`~pipeline.normalize.Event.start_date` /
:attr:`~pipeline.normalize.Event.end_date` (local, **inclusive**) next to
:attr:`~pipeline.normalize.Event.start_utc` /
:attr:`~pipeline.normalize.Event.end_utc` (instants, **exclusive** end) exactly
so this module does not have to invert the anchoring math. All-day events are
written from the dates as ``VALUE=DATE`` with RFC 5545's exclusive ``DTEND``
(inclusive last day **plus one**); timed events are written from the UTC
instants, which are the authoritative form. Writing an all-day event from
``start_utc`` would render it as 00:00 Pacific in UTC — 07:00 or 08:00 — and
put a fair number of them on the previous day for anyone east of here.

Validate, then move into place
------------------------------

:func:`emit_ics` renders **and validates every file** before it writes any of
them, then writes each one to a temp file in the destination directory and
:func:`os.replace`\\ s it into position. A half-written ``.ics`` served to
subscribers is worse than a stale one, and a per-space failure must not be able
to leave a freshly-replaced merged file next to a stale per-space one.

Implemented by issue 0011.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from icalendar import Calendar
from icalendar import Event as VEvent
from icalendar import vCalAddress, vText

from pipeline.config import OUT_DIR, Registry, Space
from pipeline.normalize import DEFAULT_TIMEZONE, Event

LOG = logging.getLogger("pipeline.emit_ics")

# --------------------------------------------------------------------------- knobs

#: From the handoff, verbatim. Subscribers' clients key off this; do not churn it.
PRODID = "-//brennan.sstools.co//maker-calendar//EN"

#: What the calendar calls itself in a client's sidebar.
CALNAME = "Bay Area Makerspaces"

#: The zone the calendar as a whole is "about". Individual timed events are
#: still emitted in UTC — this is a display hint, not a source of truth.
CALTIMEZONE = DEFAULT_TIMEZONE

#: "Roughly 300 characters plus a link" (Part 5). Roughly, because the cut lands
#: on the last word boundary at or before this, never mid-word.
DESCRIPTION_LIMIT = 300

#: Appended to a description that was cut. One character, so it costs nothing
#: against the limit.
ELLIPSIS = "…"

#: The merged calendar, relative to ``out/``.
MERGED_NAME = "calendar.ics"

#: Per-space calendars, relative to ``out/``.
SPACES_DIRNAME = "spaces"

#: Properties every published ``VEVENT`` must carry. ``URL`` is on the list
#: because an event with no link back is the one thing this project has promised
#: the spaces it will never publish.
REQUIRED_PROPERTIES = ("UID", "DTSTAMP", "DTSTART", "SUMMARY", "URL", "ORGANIZER")

#: Properties that must never appear. See "Why no ``RRULE``, ever" above.
FORBIDDEN_PROPERTIES = ("RRULE", "RDATE", "EXRULE")

UTC = dt.timezone.utc


# --------------------------------------------------------------------------- errors


class EmitError(Exception):
    """Base class for a failure to produce output. Nothing is published."""


class InvalidCalendarError(EmitError):
    """The rendered calendar failed round-trip validation.

    Raised *before* anything is moved into place, so the previously published
    file survives untouched. A stale calendar is a recoverable problem; a
    corrupt one that a hundred clients have already cached is not.
    """


class UnknownSpaceError(EmitError):
    """An event names a ``space_id`` the registry does not have.

    Fatal rather than skipped: the space is where ``URL``, ``ORGANIZER`` and the
    ``SUMMARY`` prefix come from, and an event emitted without them is exactly
    the anonymous, un-attributed entry this project exists not to publish.
    """


# --------------------------------------------------------------------------- results


@dataclass(frozen=True)
class Validation:
    """What a round-trip through ``icalendar`` found. Reported, not just asserted."""

    label: str
    event_count: int
    uids: tuple[str, ...] = ()
    space_ids: frozenset[str] = frozenset()
    all_day_count: int = 0

    @property
    def unique_uids(self) -> int:
        return len(set(self.uids))

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        return f"{self.label}: {self.event_count} events, {self.unique_uids} uids"


@dataclass(frozen=True)
class EmitResult:
    """The files written, and the accounting that justifies them.

    Issue 0012's CLI reports these counts, issue 0017 folds them into
    ``health.json``, and issue 0018 mirrors the shape for RSS.
    """

    merged_path: Path
    event_count: int = 0
    space_paths: dict[str, Path] = field(default_factory=dict)
    counts_by_space: dict[str, int] = field(default_factory=dict)
    #: Quarantined events refused at the door, by :func:`publishable`.
    skipped_quarantined: int = 0
    dtstamp: dt.datetime | None = None
    validations: tuple[Validation, ...] = ()

    @property
    def paths(self) -> tuple[Path, ...]:
        """Every file written, merged first."""
        return (self.merged_path, *self.space_paths.values())

    @property
    def space_count(self) -> int:
        return len(self.counts_by_space)

    def summary(self) -> dict[str, Any]:
        """JSON-ready counts for ``health.json`` (issue 0017)."""
        return {
            "merged": str(self.merged_path),
            "events": self.event_count,
            "spaces": self.space_count,
            "by_space": dict(self.counts_by_space),
            "skipped_quarantined": self.skipped_quarantined,
            "dtstamp": self.dtstamp.isoformat() if self.dtstamp else None,
        }

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        return (
            f"{self.merged_path.name}: {self.event_count} events across "
            f"{self.space_count} spaces"
        )


# --------------------------------------------------------------------------- spaces

#: Anything that can answer "what is space ``x``?".
SpaceLookup = Registry | Mapping[str, Space] | Iterable[Space]


def space_index(spaces: SpaceLookup) -> dict[str, Space]:
    """Normalize *spaces* to ``{space_id: Space}``.

    Accepts the whole :class:`~pipeline.config.Registry`, a mapping, or any
    iterable of spaces, so a caller never has to reshape the registry to emit.
    """
    if isinstance(spaces, Registry):
        return dict(spaces.spaces_by_id)
    if isinstance(spaces, Mapping):
        return dict(spaces)
    return {space.id: space for space in spaces}


def _space_for(event: Event, index: Mapping[str, Space]) -> Space:
    try:
        return index[event.space_id]
    except KeyError:
        raise UnknownSpaceError(
            f"event {event.uid!r} names space {event.space_id!r}, which is not in "
            "the registry. URL, ORGANIZER and the SUMMARY prefix all come from "
            "the space; emitting the event without them would publish an "
            "unattributed entry, which is the one thing we have promised not to do."
        ) from None


# --------------------------------------------------------------------------- text


def truncate(text: str | None, limit: int = DESCRIPTION_LIMIT) -> str:
    """Cut *text* to at most *limit* characters, on a word boundary.

    "Roughly 300 characters" (Part 5) rather than exactly 300: cutting mid-word
    produces the ransom-note effect that makes a merged feed look scraped, and
    the whole reason to publish a summary instead of the full description is
    that it reads better. A single trailing :data:`ELLIPSIS` marks the cut.
    """
    if not text:
        return ""
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(cleaned) <= limit:
        return cleaned

    window = cleaned[: limit + 1]
    cut = max(window.rfind(" "), window.rfind("\n"), window.rfind("\t"))
    body = window[:cut] if cut > 0 else cleaned[:limit]
    # Whitespace only. Stripping punctuation here would silently alter the last
    # word, and "the last word is intact" is the property being defended.
    return body.rstrip() + ELLIPSIS


def event_link(event: Event, space: Space, *, prefer_event_url: bool = True) -> str:
    """The link a subscriber follows. Never empty — see the module docstring."""
    if prefer_event_url and event.url:
        return event.url
    return space.url or event.url or ""


def build_summary(event: Event, space: Space) -> str:
    """``[Ace Makerspace] Sewing 101 Bootcamp``.

    The prefix is the space's registry ``name`` verbatim. Abbreviating it here
    would mean inventing short names in code that nothing else in the project
    agrees with; if a shorter label is wanted it belongs in ``sources.yaml``.
    """
    title = event.title or "Event"
    return f"[{space.name}] {title}"


def build_description(
    event: Event,
    space: Space,
    *,
    limit: int = DESCRIPTION_LIMIT,
    prefer_event_url: bool = True,
) -> str:
    """Truncated source text, then the price if known, then the link.

    The link is **always** appended, including when the source supplied no
    description at all: "link back to the source page on every event" is the
    promise, and the truncation is what keeps us clear of republishing someone
    else's copy wholesale.
    """
    link = event_link(event, space, prefer_event_url=prefer_event_url)
    body = truncate(event.description, limit)

    tail: list[str] = []
    if event.price:
        tail.append(f"Price: {event.price}")
    tail.append(f"{space.name} — {link}" if link else space.name)
    joined = "\n".join(tail)
    return f"{body}\n\n{joined}" if body else joined


# --------------------------------------------------------------------------- events


def publishable(events: Iterable[Event]) -> tuple[list[Event], int]:
    """Split off quarantined events. Returns ``(publishable, skipped)``.

    Quarantine (issue 0009) means "this extraction went wrong, look at it" —
    publishing an empty-titled or 200-character-titled event would put the
    failure in front of subscribers instead of in front of us. The count comes
    back rather than being discarded so the refusal is visible in
    :meth:`EmitResult.summary`.
    """
    kept: list[Event] = []
    skipped = 0
    for event in events:
        if event.is_quarantined:
            skipped += 1
            LOG.warning(
                "%s: not emitting quarantined event %s (%s)",
                event.space_id,
                event.uid,
                event.quarantine.value if event.quarantine else "?",
            )
            continue
        kept.append(event)
    return kept, skipped


def sort_events(events: Iterable[Event]) -> list[Event]:
    """Chronological, ties broken by UID.

    Deterministic order means byte-identical output for an unchanged event set,
    which is what lets a diff of two nights' files mean something.
    """
    return sorted(events, key=lambda item: (item.start_utc, item.uid))


def build_vevent(
    event: Event,
    space: Space,
    *,
    dtstamp: dt.datetime,
    prefer_event_url: bool = True,
    description_limit: int = DESCRIPTION_LIMIT,
) -> VEvent:
    """One ``VEVENT``. Escaping and folding are left to ``icalendar``.

    Text is handed over raw: ``icalendar`` escapes ``,``, ``;``, ``\\`` and
    newlines on the way out and folds long lines per RFC 5545. Pre-escaping
    would double every backslash, and a space name with a comma in it is likely
    enough that the round-trip is tested rather than assumed.
    """
    component = VEvent()

    # UID verbatim. It is the subscriber-stability contract: transform it and
    # every subscriber sees every event as new tonight.
    component.add("uid", event.uid)
    component.add("dtstamp", dtstamp.astimezone(UTC))

    if event.all_day:
        # VALUE=DATE from the local dates, with RFC 5545's exclusive DTEND.
        start_date = event.start_date or event.start_local.date()
        end_date = event.end_date or start_date
        component.add("dtstart", start_date)
        component.add("dtend", end_date + dt.timedelta(days=1))
    else:
        # start_utc is authoritative for a timed event, so emit the instant.
        component.add("dtstart", event.start_utc.astimezone(UTC))
        if event.end_utc is not None:
            component.add("dtend", event.end_utc.astimezone(UTC))

    component.add("summary", build_summary(event, space))
    component.add(
        "description",
        build_description(
            event,
            space,
            limit=description_limit,
            prefer_event_url=prefer_event_url,
        ),
    )

    location = event.address or event.location_name
    if location:
        component.add("location", location)

    component.add("url", event_link(event, space, prefer_event_url=prefer_event_url))

    organizer = vCalAddress(space.url or "")
    organizer.params["CN"] = vText(space.name)
    component.add("organizer", organizer)

    if event.categories:
        component.add("categories", list(event.categories))

    # Provenance, for health reporting (0017) and for answering "which feed
    # produced this?" from the published file alone.
    component.add("x-maker-space-id", event.space_id)
    if event.source_label:
        component.add("x-maker-source", event.source_label)

    # Deliberately absent: RRULE. See the module docstring.
    return component


def build_calendar(
    events: Iterable[Event],
    spaces: SpaceLookup,
    *,
    name: str = CALNAME,
    timezone: str = CALTIMEZONE,
    dtstamp: dt.datetime | None = None,
    prefer_event_url: bool = True,
    description_limit: int = DESCRIPTION_LIMIT,
) -> Calendar:
    """Assemble one ``VCALENDAR`` from *events*."""
    index = space_index(spaces)
    dtstamp = (dtstamp or dt.datetime.now(UTC)).astimezone(UTC)

    calendar = Calendar()
    calendar.add("prodid", PRODID)
    calendar.add("version", "2.0")
    calendar.add("calscale", "GREGORIAN")
    calendar.add("method", "PUBLISH")
    calendar["X-WR-CALNAME"] = vText(name)
    calendar["X-WR-TIMEZONE"] = vText(timezone)

    for event in sort_events(events):
        calendar.add_component(
            build_vevent(
                event,
                _space_for(event, index),
                dtstamp=dtstamp,
                prefer_event_url=prefer_event_url,
                description_limit=description_limit,
            )
        )
    return calendar


def render(calendar: Calendar) -> bytes:
    """Serialize to CRLF-delimited iCalendar bytes."""
    return calendar.to_ical()


# --------------------------------------------------------------------------- validate


def validate_ics(
    data: bytes | str,
    *,
    expected_count: int,
    tolerance: int = 0,
    label: str = "calendar",
) -> Validation:
    """Round-trip *data* through ``icalendar`` and assert it is publishable.

    The gate the handoff asks for, plus the assertions ``verify`` describes:
    it parses, the event count is within *tolerance* of what we meant to emit,
    every ``VEVENT`` carries :data:`REQUIRED_PROPERTIES`, no ``VEVENT`` carries
    :data:`FORBIDDEN_PROPERTIES`, UIDs are unique and non-empty, timed starts
    are timezone-aware and all-day starts are real ``VALUE=DATE`` dates.

    Raises :class:`InvalidCalendarError` on any of those. Never writes, never
    repairs — a caller that catches this must leave the live file alone.
    """
    payload = data.encode("utf-8") if isinstance(data, str) else data

    try:
        calendar = Calendar.from_ical(payload)
    except (ValueError, TypeError) as exc:
        raise InvalidCalendarError(
            f"{label}: does not parse as iCalendar ({exc}). Nothing was "
            "published; the previous file is still in place."
        ) from exc

    if calendar.name != "VCALENDAR":
        raise InvalidCalendarError(
            f"{label}: root component is {calendar.name!r}, not VCALENDAR"
        )
    for required in ("PRODID", "VERSION"):
        if not calendar.get(required):
            raise InvalidCalendarError(f"{label}: VCALENDAR has no {required}")

    vevents = list(calendar.walk("VEVENT"))
    drift = abs(len(vevents) - expected_count)
    if drift > tolerance:
        raise InvalidCalendarError(
            f"{label}: round-trip found {len(vevents)} VEVENTs, expected "
            f"{expected_count} (tolerance {tolerance}). A count that moves "
            "between building and re-reading means the serializer and the "
            "parser disagree, and neither of them is worth trusting until "
            "that is understood."
        )

    uids: list[str] = []
    space_ids: set[str] = set()
    all_day = 0

    for index, vevent in enumerate(vevents):
        where = f"{label}: VEVENT {index}"
        for prop in REQUIRED_PROPERTIES:
            if vevent.get(prop) is None:
                raise InvalidCalendarError(f"{where} has no {prop}")
        for prop in FORBIDDEN_PROPERTIES:
            if vevent.get(prop) is not None:
                raise InvalidCalendarError(
                    f"{where} carries {prop}. Recurrence is expanded upstream; "
                    "emitting the rule as well would duplicate the series in "
                    "every client that honors it."
                )

        uid = str(vevent.get("UID")).strip()
        if not uid:
            raise InvalidCalendarError(f"{where} has an empty UID")
        uids.append(uid)

        start = vevent.get("DTSTART")
        value = start.dt
        if isinstance(value, dt.datetime):
            if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
                raise InvalidCalendarError(
                    f"{where} ({uid}) has a naive DTSTART {value.isoformat()}. A "
                    "naive datetime in the output means normalize was bypassed."
                )
        elif isinstance(value, dt.date):
            all_day += 1
            if start.params.get("VALUE") != "DATE":
                raise InvalidCalendarError(
                    f"{where} ({uid}) is all-day but its DTSTART is missing "
                    "VALUE=DATE"
                )
            end = vevent.get("DTEND")
            if end is not None and not end.dt > value:
                raise InvalidCalendarError(
                    f"{where} ({uid}) has DTEND {end.dt} which is not after "
                    f"DTSTART {value}. RFC 5545's all-day DTEND is exclusive."
                )
        else:  # pragma: no cover - icalendar yields only date/datetime
            raise InvalidCalendarError(f"{where} ({uid}) has an unusable DTSTART")

        marker = vevent.get("X-MAKER-SPACE-ID")
        if marker is not None:
            space_ids.add(str(marker))

    duplicates = sorted({uid for uid in uids if uids.count(uid) > 1})
    if duplicates:
        raise InvalidCalendarError(
            f"{label}: duplicate UIDs {duplicates[:5]}. Clients treat one UID as "
            "one event, so a duplicate silently loses an occurrence."
        )

    validation = Validation(
        label=label,
        event_count=len(vevents),
        uids=tuple(uids),
        space_ids=frozenset(space_ids),
        all_day_count=all_day,
    )
    LOG.debug("%s validated: %s", label, validation)
    return validation


# --------------------------------------------------------------------------- write


def write_atomic(path: Path, data: bytes) -> Path:
    """Write *data* to *path* through a temp file in the same directory.

    :func:`os.replace` is atomic within a filesystem, so a subscriber polling
    the file sees either the old bytes or the new ones and never a prefix of
    the new ones. The temp file is created beside the target rather than in
    ``/tmp`` precisely so the rename cannot cross a filesystem boundary and
    silently degrade into a copy.

    Any failure removes the temp file and re-raises, leaving whatever was at
    *path* untouched.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    handle, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return path


# --------------------------------------------------------------------------- emit


def emit_ics(
    events: Iterable[Event],
    *,
    spaces: SpaceLookup,
    out_dir: Path | str = OUT_DIR,
    dtstamp: dt.datetime | None = None,
    tolerance: int = 0,
    per_space: bool = True,
    prefer_event_url: bool = True,
    description_limit: int = DESCRIPTION_LIMIT,
    name: str = CALNAME,
    timezone: str = CALTIMEZONE,
) -> EmitResult:
    """Render, validate, then atomically publish the merged and per-space files.

    The entry point. Issue 0012's CLI calls this; issue 0017 reads
    :meth:`EmitResult.summary`.

    Every file is rendered and validated **before** any of them is moved into
    place, so a failure anywhere leaves the whole of ``out/`` as it was. Raises
    :class:`InvalidCalendarError` if the round-trip disagrees with what was
    built, and :class:`UnknownSpaceError` if an event names a space the registry
    does not have.
    """
    out_dir = Path(out_dir)
    index = space_index(spaces)
    dtstamp = (dtstamp or dt.datetime.now(UTC)).astimezone(UTC)

    kept, skipped = publishable(events)
    ordered = sort_events(kept)
    for event in ordered:
        _space_for(event, index)  # fail before rendering anything

    merged_path = out_dir / MERGED_NAME
    plan: list[tuple[Path, bytes]] = []
    validations: list[Validation] = []

    merged = render(
        build_calendar(
            ordered,
            index,
            name=name,
            timezone=timezone,
            dtstamp=dtstamp,
            prefer_event_url=prefer_event_url,
            description_limit=description_limit,
        )
    )
    validations.append(
        validate_ics(
            merged,
            expected_count=len(ordered),
            tolerance=tolerance,
            label=str(merged_path),
        )
    )
    plan.append((merged_path, merged))

    by_space: dict[str, list[Event]] = {}
    for event in ordered:
        by_space.setdefault(event.space_id, []).append(event)

    space_paths: dict[str, Path] = {}
    if per_space:
        for space_id, space_events in by_space.items():
            space = index[space_id]
            path = out_dir / SPACES_DIRNAME / f"{space_id}.ics"
            data = render(
                build_calendar(
                    space_events,
                    index,
                    name=f"{name} — {space.name}",
                    timezone=timezone,
                    dtstamp=dtstamp,
                    prefer_event_url=prefer_event_url,
                    description_limit=description_limit,
                )
            )
            validations.append(
                validate_ics(
                    data,
                    expected_count=len(space_events),
                    tolerance=tolerance,
                    label=str(path),
                )
            )
            plan.append((path, data))
            space_paths[space_id] = path

    for path, data in plan:
        write_atomic(path, data)

    result = EmitResult(
        merged_path=merged_path,
        event_count=len(ordered),
        space_paths=space_paths,
        counts_by_space={sid: len(items) for sid, items in by_space.items()},
        skipped_quarantined=skipped,
        dtstamp=dtstamp,
        validations=tuple(validations),
    )
    LOG.info(
        "emitted %d events across %d spaces to %s (%d quarantined events withheld)",
        result.event_count,
        result.space_count,
        merged_path,
        skipped,
    )
    return result


def emit_string(
    events: Sequence[Event],
    spaces: SpaceLookup,
    *,
    dtstamp: dt.datetime | None = None,
    **kwargs: Any,
) -> bytes:
    """Render a calendar to bytes without touching the filesystem.

    For the dry-run path (issue 0012) and for anything that wants the artifact
    without publishing it.
    """
    return render(build_calendar(events, spaces, dtstamp=dtstamp, **kwargs))


__all__ = [
    "CALNAME",
    "CALTIMEZONE",
    "DESCRIPTION_LIMIT",
    "ELLIPSIS",
    "FORBIDDEN_PROPERTIES",
    "MERGED_NAME",
    "PRODID",
    "REQUIRED_PROPERTIES",
    "SPACES_DIRNAME",
    "EmitError",
    "EmitResult",
    "InvalidCalendarError",
    "SpaceLookup",
    "UnknownSpaceError",
    "Validation",
    "build_calendar",
    "build_description",
    "build_summary",
    "build_vevent",
    "emit_ics",
    "emit_string",
    "event_link",
    "publishable",
    "render",
    "sort_events",
    "space_index",
    "truncate",
    "validate_ics",
    "write_atomic",
]
