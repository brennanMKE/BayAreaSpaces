"""Google Calendar adapter: build the ``basic.ics`` URL, then delegate to ``ics``.

A deliberately thin wrapper. Google's public export *is* an iCalendar feed, so
there is nothing to parse here that :mod:`pipeline.adapters.ics` does not already
do better. The one thing this module owns is the seam the registry actually
carries: a ``calendar_id`` rather than a ``url``.

    https://calendar.google.com/calendar/ical/{quote(calendar_id)}/public/basic.ics

Registered consumers (2026-08-05 survey):

===============================================  ==========================================
``sequoia-fabrica`` / ``community-calendar``     89 VEVENTs back to 2023-12, ~7 live in a
                                                 120-day horizon, 6 of them open-ended
                                                 monthly RRULEs, mixed ``DTSTART`` forms
``maker-nexus`` / ``amilia-published-classes``   11 MB, 3645 VEVENTs, 171 in the future,
                                                 pre-expanded (no RRULEs), history to 2023-12
``noisebridge`` / ``noisebridge-today``          ``enabled: false``, deliberately — dead since
                                                 2024-01 with five ``UNTIL``-less RRULEs
===============================================  ==========================================

The ``@`` is the whole job
--------------------------

A Google calendar id looks like an email address, and the path segment it becomes
must have its ``@`` percent-encoded. The registry contains **both** spellings —
``sources.yaml`` writes the literal ``@``, while ``references/feeds.json`` and
``spaces/*.md`` record the same calendars with ``%40`` — so this module normalizes
before encoding: :func:`normalize_calendar_id` decodes first, and
:func:`encode_calendar_id` encodes the decoded form. Both spellings therefore
produce one identical request URL, and an already-encoded id is never turned into
``%2540``.

That mistake is not cosmetic. ``%2540group.calendar.google.com`` is a valid
request for a calendar that does not exist, and Google answers it with a 404 —
which, before :func:`parse_gcal_ics`'s status check below, would surface as a
space quietly missing from the calendar. "Never guess a feed URL": this is the
only URL in the project that is constructed rather than read, it is a fixed
Google template, and :func:`pipeline.fetch.resolve_url` delegates here so the
fetch layer and the adapter can never disagree about what was requested.

``X-WR-CALNAME`` is not a label
-------------------------------

:class:`~pipeline.adapters.ics.IcsParse` already surfaces ``X-WR-CALNAME``,
``X-WR-CALDESC`` and the newest ``LAST-MODIFIED``, and all three matter here:
issue 0016's ``max_stale_days`` gate runs on the last one, and the descriptions
carry real information — Sequoia Fabrica's ``X-WR-CALDESC`` reads "For member and
volunteer events (separate from classes/Bookwhen events)", which is what proved
that calendar is disjoint from its Bookwhen feed.

**But the calendar name is never a source label.** Humanmade's Luma calendar is
named "Personal" and Maker Nexus's is "Amilia Published Classes"; the registry's
``label`` is authoritative and is what :attr:`IcsParse.label` carries.

Implemented by issue 0008.
"""

from __future__ import annotations

import datetime as dt
from urllib.parse import quote, unquote

import httpx

from pipeline.adapters.ics import (
    DEFAULT_HORIZON_DAYS,
    IcsParse,
    IcsProblem,
    parse_ics,
    parse_ics_text,
)
from pipeline.config import Source, SourceRef
from pipeline.fetch import GCAL_ICS_TEMPLATE, FetchResult, Fetcher, Outcome

# --------------------------------------------------------------------------- knobs

#: The only host this adapter ever builds a URL for.
GCAL_HOST = "calendar.google.com"

#: The ``@`` in a calendar id, and what it must become in a path segment. Named
#: because the encoding of exactly this character is the point of the module.
CALENDAR_ID_AT = "@"
CALENDAR_ID_AT_ENCODED = "%40"

__all__ = [
    "CALENDAR_ID_AT",
    "CALENDAR_ID_AT_ENCODED",
    "GCAL_HOST",
    "GCAL_ICS_TEMPLATE",
    "InvalidCalendarId",
    "calendar_ics_url",
    "encode_calendar_id",
    "fetch_gcal_ics",
    "normalize_calendar_id",
    "parse",
    "parse_gcal_ics",
    "parse_gcal_ics_text",
    "source_calendar_id",
    "source_ics_url",
]


class InvalidCalendarId(ValueError):
    """The registry's ``calendar_id`` is empty, or is not a calendar id at all."""


# --------------------------------------------------------------------------- the URL


def normalize_calendar_id(calendar_id: str) -> str:
    """The canonical, *decoded* calendar id.

    ``c_69d095%40group.calendar.google.com`` and
    ``c_69d095@group.calendar.google.com`` both come back as the second form, so
    the two spellings in this project's own files name one calendar rather than
    two. Idempotent by construction, which is what stops :func:`encode_calendar_id`
    producing ``%2540``.

    Raises :class:`InvalidCalendarId` for an empty id or for a full URL — a
    ``calendar_id`` holding ``https://calendar.google.com/...`` would otherwise be
    percent-encoded wholesale into a plausible-looking request for a calendar that
    does not exist.
    """
    if not isinstance(calendar_id, str):  # pragma: no cover - defensive
        raise InvalidCalendarId(f"calendar_id must be a string, got {type(calendar_id).__name__}")

    raw = calendar_id.strip()
    if not raw:
        raise InvalidCalendarId("calendar_id is empty; gcal_ics has nothing to fetch")

    decoded = unquote(raw)

    if "://" in decoded or decoded.lower().startswith(("http:", "https:")):
        raise InvalidCalendarId(
            f"calendar_id {calendar_id!r} looks like a URL. gcal_ics takes the bare "
            "calendar id (the part before /public/basic.ics); a URL here would be "
            "percent-encoded into a path segment and 404."
        )
    if "/" in decoded:
        raise InvalidCalendarId(
            f"calendar_id {calendar_id!r} contains '/', so it is a path fragment rather "
            "than a calendar id"
        )
    if any(char.isspace() for char in decoded):
        raise InvalidCalendarId(f"calendar_id {calendar_id!r} contains whitespace")

    return decoded


def encode_calendar_id(calendar_id: str) -> str:
    """The calendar id as a single URL path segment — ``@`` becomes ``%40``.

    ``safe=""`` because the id is one path segment and nothing in it is
    structural: a stray ``/`` or ``?`` must be encoded, not obeyed.
    """
    return quote(normalize_calendar_id(calendar_id), safe="")


def calendar_ics_url(calendar_id: str) -> str:
    """The public ``basic.ics`` URL for ``calendar_id``.

    Pure and importable on its own: no HTTP, no registry, no fetch layer. This is
    the function to reach for when checking what *would* be requested.
    """
    return GCAL_ICS_TEMPLATE.format(calendar_id=encode_calendar_id(calendar_id))


def source_calendar_id(source: Source) -> str:
    """The registry entry's calendar id, normalized. Raises if it has none."""
    if source.adapter != "gcal_ics":
        raise InvalidCalendarId(
            f"source adapter is {source.adapter!r}, not 'gcal_ics'; "
            "gcal_ics only handles sources that carry a calendar_id"
        )
    if not source.calendar_id:
        raise InvalidCalendarId("gcal_ics source carries no calendar_id")
    return normalize_calendar_id(source.calendar_id)


def source_ics_url(source: Source) -> str:
    """The URL a registered ``gcal_ics`` source is fetched from."""
    return calendar_ics_url(source_calendar_id(source))


# --------------------------------------------------------------------------- parsing


def _failure(
    problem: IcsProblem,
    error: str,
    *,
    result: FetchResult,
    horizon_days: int,
    today: dt.date,
    now: dt.datetime,
) -> IcsParse:
    """A reported failure in the same shape ``ics`` uses. Never raised."""
    return IcsParse(
        problem=problem,
        error=error,
        space_id=result.space_id,
        label=result.label,
        source_url=result.url,
        horizon_days=horizon_days,
        window_start=today,
        window_end=today + dt.timedelta(days=horizon_days),
        parsed_at=now,
    )


def parse_gcal_ics(
    result: FetchResult,
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    today: dt.date | None = None,
    now: dt.datetime | None = None,
    strict_content_type: bool = True,
) -> IcsParse:
    """Parse a fetched Google Calendar export. Delegates to ``ics``.

    Adds exactly one judgement of its own before delegating: **a non-2xx status
    from ``calendar.google.com`` is a reported failure, never an empty calendar.**

    CLAUDE.md's "HTTP 200 is not success" has a mirror image that matters here.
    Elsewhere in this registry a 404 can carry a perfectly good body — one source
    answers 404 with valid, populated RSS — so the fetch layer hands the status and
    the body over separately and refuses to pre-judge. Google is not that case: its
    404 is an HTML error page, and it is also the project's **negative control**.
    Maker Nexus's calendar id was confirmed genuine by checking that a bogus id
    404s, and the same technique proved Ace's category feeds actually filter. A
    404 here therefore means the id is wrong, the calendar was un-shared, or the
    id was double-encoded — all of which need a human, and none of which may look
    like a space that simply has no events on.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    today = today or now.date()

    if result.outcome is Outcome.FETCHED and result.status_code is not None:
        if not 200 <= result.status_code < 300:
            return _failure(
                IcsProblem.NOT_CALENDAR,
                f"{result.source_key}: HTTP {result.status_code} from {result.url} — "
                "Google Calendar answers a wrong, un-shared or double-encoded "
                "calendar id with an error page, so this is a broken source and not "
                "an empty calendar (a bogus id 404s; that is the negative control "
                "this registry's ids were verified with)",
                result=result,
                horizon_days=horizon_days,
                today=today,
                now=now,
            )

    return parse_ics(
        result,
        horizon_days=horizon_days,
        today=today,
        now=now,
        strict_content_type=strict_content_type,
    )


#: Parse Google Calendar iCalendar *text*, with no HTTP in it. Re-exported from
#: ``ics`` unchanged: once the bytes are in hand there is nothing Google-specific
#: left, and a second spelling of the same function would only drift.
parse_gcal_ics_text = parse_ics_text


# --------------------------------------------------------------------------- fetching


def fetch_gcal_ics(
    fetcher: Fetcher,
    ref: SourceRef,
    *,
    horizon_days: int | None = None,
    today: dt.date | None = None,
    now: dt.datetime | None = None,
    conditional: bool = True,
    strict_content_type: bool = True,
) -> IcsParse:
    """Build the URL for a registered source, fetch it, and parse the result.

    Goes through :meth:`pipeline.fetch.Fetcher.fetch_url` so the URL this module
    constructs is still subject to the same ``robots.txt`` decision, the same
    per-host rate limit and the same ``raw/`` archiving as every other source.

    ``conditional=True`` by default, unlike ``fetch_url``'s own default: Maker
    Nexus's export is 11 MB with history back to 2023-12, and a 304 is the
    difference between reading it and not. A 304 comes back as an
    :class:`~pipeline.adapters.ics.IcsParse` with
    :attr:`~pipeline.adapters.ics.IcsProblem.NOT_MODIFIED`, which issue 0014 reads
    as "reuse the stored events" — not as zero.
    """
    _space, source = ref
    url = httpx.URL(source_ics_url(source))
    if horizon_days is None:
        horizon_days = fetcher.registry.horizon_days

    result = fetcher.fetch_url(ref, url, conditional=conditional)
    return parse_gcal_ics(
        result,
        horizon_days=horizon_days,
        today=today,
        now=now,
        strict_content_type=strict_content_type,
    )


#: The adapter entry point named by ``sources.yaml``'s ``adapter: gcal_ics``.
parse = parse_gcal_ics
