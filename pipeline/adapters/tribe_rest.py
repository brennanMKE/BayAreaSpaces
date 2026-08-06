"""The Events Calendar (WordPress) REST API — ``/wp-json/tribe/events/v1/events``.

The richest source in the registry. Ace Makerspace runs WordPress with The
Events Calendar ("TEC", vendor prefix ``tribe``) 6.17.0, and its REST API
carries everything the same site's ICS export does *plus* venue objects with
postal addresses, organizers, per-event ``cost`` text, category objects,
``is_virtual`` and ticket status. That is why ``tribe-rest`` is ``trust: 100``
and ``tribe-ics-list`` is ``trust: 90``: same events, strictly more fields, so
dedupe (issue 0015) keeps the REST copy when the two collide.

::

    GET /wp-json/tribe/events/v1/events?start_date=YYYY-MM-DD&per_page=50

Registered consumer (2026-08-05 survey):

==================================  =========================================
``ace-makerspace`` / ``tribe-rest``  **92 upcoming events over 2 pages**,
                                     ``total_pages: 2``, 50 per page,
                                     ``end_date`` defaulting to +2 years
==================================  =========================================

Detection heuristic, for the next space
---------------------------------------

**A ``?ical=1`` subscribe link, or ``tec-api-version`` in the page meta, means
both an ICS feed and this REST API exist.** Ace was found exactly that way: the
``tec-api-version: v1`` and ``tec-api-origin`` meta tags on ``/calendar/`` said
TEC was installed before a single ``wp-json`` route was requested, and the ICS
``PRODID`` (``-//Ace Makerspace - ECPv6.17.0//NONSGML v1.0//EN``) confirmed the
version. When both exist, register both — REST at the higher trust, ICS as the
fallback — and never ingest them as two independent sources.

The counter-example, which costs an afternoon if you miss it
------------------------------------------------------------

**The Crucible's pages are full of ``tribe-*`` CSS class names and TEC is not
installed.** Every ``wp-json/tribe/*`` route on ``thecrucible.org`` returns 404.
Those class names are inert Avada theme compatibility CSS shipped by the theme
whether or not the plugin is present — markup, not evidence. ``sources.yaml``
records the same warning next to that space, and the check that settles it is
one request:

.. code-block:: console

    $ curl -s -o /dev/null -w '%{http_code}\\n' \\
        https://www.thecrucible.org/wp-json/tribe/events/v1/events
    404

A 404 from this endpoint is therefore reported as
:attr:`TribeProblem.HTTP_ERROR` with that sentence attached, and never as an
empty calendar. "A wrong adapter is as silent as a wrong URL."

``cost`` is text, and stays text
--------------------------------

``cost`` arrives HTML-escaped — Ace returns ``&#036;20.00`` — so every string
field here goes through :func:`html.unescape`. What does **not** happen is
parsing it to a number. "sliding scale $10–30" and "free for members" are real
values in this dataset, and :attr:`pipeline.normalize.Event.price` is documented
as source text for exactly that reason. A float would quietly turn a range into
a wrong number and a sentence into ``None``.

Pagination
----------

**Follow the response's own ``next_rest_url``**, never a constructed
``?page=N``: TEC embeds the query it actually ran (including ``start_date`` and
``end_date`` defaults) in that link, and reconstructing it is another chance to
silently request a different window than the one page 1 came from. Pages are
fetched through :meth:`pipeline.fetch.Fetcher.fetch_url`, so page 2 obeys the
same ``robots.txt`` decision and the same 10-second Ace rate limit, and lands in
``raw/`` under its own name.

Three stop conditions, and they are not the same thing:

- **No ``next_rest_url``** — the ordinary end. Ace's page 2 has none.
- **A 200 with an empty ``events`` list** — also the end, *not* an error. A TEC
  site whose last page lands exactly on the page boundary answers this way.
- **:data:`MAX_PAGES` reached, or a repeated URL** — a server that always offers
  a next page. Reported as :attr:`TribeProblem.RUNAWAY_PAGINATION` rather than
  quietly returning whatever we happened to collect, because at that point the
  count means nothing and a human needs to look at the feed.

A failure on page 2 or later fails the whole parse (``ok=False``), so issue
0014's carry-forward republishes last night's complete 92 rather than tonight's
half of them. Half a calendar that looks healthy is the failure mode this
project keeps designing against.

Implemented by issue 0019.
"""

from __future__ import annotations

import datetime as dt
import html
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from pipeline.adapters.ics import DEFAULT_HORIZON_DAYS, IcsEvent, IcsParse
from pipeline.config import SourceRef
from pipeline.fetch import (
    JSON_CONTENT_TYPES,
    ContentTypeMismatch,
    FetchResult,
    Fetcher,
    Outcome,
)

# --------------------------------------------------------------------------- knobs

#: The route TEC exposes. Present iff the plugin is installed — see the module
#: docstring for the Crucible counter-example.
TRIBE_EVENTS_PATH = "/wp-json/tribe/events/v1/events"

#: What ``sources.yaml`` asks for, and what Ace was surveyed at. TEC's own
#: default is 10, which would turn 92 events into 10 pages of polite waiting.
DEFAULT_PER_PAGE = 50

#: Hard ceiling on pages per source, whatever the server keeps offering. At
#: :data:`DEFAULT_PER_PAGE` this is 1000 events — an order of magnitude above
#: the largest TEC feed in this registry (Ace, 92 over 2 pages). It exists so a
#: server that returns ``next_rest_url`` forever cannot loop the nightly run.
MAX_PAGES = 20

#: Types we tolerate **only** when the body actually parses as a JSON object.
#: Some hosts serve JSON as ``text/plain``; that is sloppy, not dishonest.
#: ``text/html`` is deliberately not here — see :data:`TribeProblem.WRONG_CONTENT_TYPE`.
LENIENT_CONTENT_TYPES: tuple[str, ...] = ("text/plain", "application/octet-stream")

#: How far into a body to look before deciding it is not JSON at all.
_SNIFF_BYTES = 4096


# --------------------------------------------------------------------------- outcomes


class TribeProblem(str, Enum):
    """Why a :class:`TribeParse` is not usable. ``NONE`` means it is.

    The first five values mirror :class:`~pipeline.adapters.ics.IcsProblem` so
    the CLI's ``record.problem`` vocabulary stays one vocabulary; the rest are
    the failures only a JSON API can have. The *kind* matters downstream: a
    ``TRANSPORT`` failure carries forward (issue 0014), an ``HTTP_ERROR`` from
    this endpoint usually means the plugin is not installed and wants a human.
    """

    NONE = "none"
    TRANSPORT = "transport"
    NOT_MODIFIED = "not_modified"
    EMPTY_BODY = "empty_body"
    WRONG_CONTENT_TYPE = "wrong_content_type"

    #: Non-2xx. On this route that is nearly always "TEC is not installed".
    HTTP_ERROR = "http_error"
    #: The body is not JSON — the 200-with-a-rendered-page case.
    NOT_JSON = "not_json"
    #: Valid JSON, but not a TEC events document (no ``events`` array). WP's
    #: ``{"code": "rest_no_route"}`` error object lands here when it arrives
    #: with a 200, as does a ``wp/v2`` payload requested by mistake.
    NOT_TRIBE = "not_tribe"
    #: ``next_rest_url`` never stopped, or pointed back at a page already read.
    RUNAWAY_PAGINATION = "runaway_pagination"
    #: Page 1 was fine; a later page failed. Partial data is never returned as
    #: if it were whole.
    PAGE_FAILED = "page_failed"


# --------------------------------------------------------------------------- events


@dataclass(frozen=True)
class TribeEvent(IcsEvent):
    """One TEC event, in the intermediate shape the ``ics`` adapter produces.

    A subclass rather than a parallel class **on purpose**: ``normalize.py``
    consumes ICS and Tribe events through one function
    (:func:`~pipeline.normalize.from_ics_event`), and the moment the two shapes
    diverge, one of them starts losing fields nobody notices for a month.
    Everything TEC adds that ICS has no room for is appended below.
    """

    #: TEC's numeric post id. Part of :attr:`uid` via ``global_id``.
    event_id: int | None = None

    #: **Source text, HTML-unescaped, never parsed to a number.**
    #: ``&#036;20.00`` → ``$20.00``; "sliding scale $10-30" survives verbatim.
    cost: str | None = None
    #: ``cost_details.currency_symbol``, when TEC supplies one. Diagnostics
    #: only — it must not be used to reconstruct a numeric price.
    currency_symbol: str | None = None
    #: TEC's ``ticketed``: this event sells tickets through the site.
    ticketed: bool = False
    #: TEC's ``is_virtual``: online rather than in the shop.
    is_virtual: bool = False
    featured: bool = False

    venue_name: str | None = None
    #: The venue's postal address, comma-joined. Ace has 8 venues and three of
    #: them are off-site, so this is not always the space's own address.
    venue_address: str | None = None

    #: Every organizer name. :attr:`~pipeline.adapters.ics.IcsEvent.organizer`
    #: carries the first, for parity with the ICS ``ORGANIZER`` property.
    organizers: tuple[str, ...] = ()

    #: TEC's ``website`` field: an external link the organizer set, distinct
    #: from :attr:`~pipeline.adapters.ics.IcsEvent.url`, which is the permalink.
    website: str | None = None
    image: str | None = None

    #: Which page of the paginated response this event came from. 1-based.
    page: int = 1

    @property
    def price(self) -> str | None:
        """Alias :attr:`cost` under the canonical schema's name.

        :func:`pipeline.normalize.from_ics_event` reads ``price`` off whatever
        the adapter handed it, so this is the one line that carries "$20.00"
        all the way to :attr:`pipeline.normalize.Event.price`.
        """
        return self.cost


# --------------------------------------------------------------------------- pages


@dataclass(frozen=True)
class TribePage:
    """One decoded page of the paginated response.

    Kept as its own object so pagination is testable without HTTP and so the
    parse can report *which* page went wrong.
    """

    url: str
    number: int = 1
    items: tuple[dict[str, Any], ...] = ()
    total: int | None = None
    total_pages: int | None = None
    next_url: str | None = None
    previous_url: str | None = None
    rest_url: str | None = None

    def __len__(self) -> int:
        return len(self.items)


# --------------------------------------------------------------------------- result


@dataclass(frozen=True)
class TribeParse(IcsParse):
    """The adapter's return value. Same surface as :class:`IcsParse`, plus pages.

    Subclasses :class:`~pipeline.adapters.ics.IcsParse` so every consumer
    already written against the ICS adapter — ``normalize_ics``, the CLI's
    ``SourceRecord``, issue 0016's gates — works unchanged.
    """

    problem: TribeProblem = TribeProblem.NONE  # type: ignore[assignment]

    #: ``total`` as the API reported it: 92 for Ace. Compared against what we
    #: actually decoded, because a disagreement means pagination lost events.
    reported_total: int | None = None
    #: ``total_pages`` as the API reported it: 2 for Ace.
    reported_pages: int | None = None
    #: One entry per page actually read, in order.
    pages: tuple[TribePage, ...] = ()
    #: True when a ``next_rest_url`` was offered and deliberately not followed
    #: — a single-page call with no fetcher. Never true inside a nightly run.
    truncated: bool = False

    @property
    def ok(self) -> bool:
        """True when the payload was a TEC events document and we decoded it.

        Zero events with ``ok=True`` is legitimate: a space with nothing on
        answers 200 with ``"events": []``. That is ``allow_zero``'s problem.
        """
        return self.problem is TribeProblem.NONE

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def counts_agree(self) -> bool:
        """Whether ``total`` matches the number of events actually decoded.

        Not a gate — TEC's ``total`` counts the whole query window (Ace's
        ``end_date`` defaults to +2 years) while :attr:`vevent_count` counts
        what pagination handed us. Equal in the observed case, and worth
        looking at when it is not.
        """
        return self.reported_total is None or self.reported_total == self.vevent_count

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        head = f"{self.space_id}:{self.label}" if self.space_id or self.label else "<tribe>"
        if not self.ok:
            return f"{head} FAILED {self.problem.value}: {self.error}"
        return (
            f"{head} {self.event_count} events in {self.horizon_days}d "
            f"(from {self.vevent_count} across {self.page_count} pages)"
        )


# --------------------------------------------------------------------------- helpers


def looks_like_json_object(text: str) -> bool:
    """True if the body opens with ``{``.

    The JSON half of "HTTP 200 is not success". A WordPress site answering this
    route with a rendered page returns 200 and a body starting ``<!DOCTYPE``;
    the content type usually gives that away, but not always, so the body is
    sniffed either way.
    """
    return text[:_SNIFF_BYTES].lstrip("﻿ \t\r\n").startswith("{")


def _clean(value: Any) -> str | None:
    """Trim and HTML-unescape one text field, or ``None`` if it is empty.

    Every string TEC returns goes through here. ``&#036;20.00`` is the reason —
    but titles carry ``&#8211;`` en dashes and ``&amp;`` just as often, and a
    calendar full of ``&amp;`` is the kind of thing nobody files a bug for.
    """
    if value is None or isinstance(value, (dict, list)):
        return None
    text = html.unescape(str(value)).strip()
    return text or None


def _bool(value: Any) -> bool:
    """TEC sends real booleans; WordPress filters sometimes send ``"1"``."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return False


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _zone(name: Any) -> ZoneInfo | None:
    """``"America/Los_Angeles"`` → a zone, or ``None`` for anything unusable.

    Never raises. An unknown zone means the UTC instant is used as-is, which is
    correct-but-less-readable; ``normalize.py`` is where a *naive* datetime
    becomes a hard error, and nothing here ever produces one.
    """
    text = _clean(name)
    if not text:
        return None
    try:
        return ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return None


def _parse_naive(value: Any) -> dt.datetime | None:
    """Parse TEC's ``"YYYY-MM-DD HH:MM:SS"`` into a naive datetime."""
    text = _clean(value)
    if not text:
        return None
    candidate = text.replace(" ", "T", 1)
    if candidate.endswith("Z"):
        candidate = candidate[:-1]
    try:
        parsed = dt.datetime.fromisoformat(candidate)
    except ValueError:
        return None
    return parsed.replace(tzinfo=None)


def _parse_utc(value: Any) -> dt.datetime | None:
    """Parse a ``utc_*`` field into an **aware** UTC datetime.

    TEC's ``utc_start_date`` / ``utc_end_date`` / ``date_utc`` / ``modified_utc``
    are UTC by definition and carry no offset in the text. Attaching the zone
    here is what keeps a naive value from ever leaving this module.
    """
    naive = _parse_naive(value)
    if naive is None:
        return None
    return naive.replace(tzinfo=dt.timezone.utc)


def _parse_date(value: Any) -> dt.date | None:
    naive = _parse_naive(value)
    return naive.date() if naive is not None else None


def _names(value: Any, key: str = "name") -> tuple[str, ...]:
    """Names out of TEC's ``[{...}]`` / ``{...}`` / ``"..."`` polymorphism.

    ``organizer`` is a list of objects, ``venue`` is one object, and a site with
    a single organizer has been observed sending the object bare rather than in
    a list. All three shapes mean the same thing.
    """
    if value is None:
        return ()
    entries: Iterable[Any]
    if isinstance(value, dict):
        entries = [value]
    elif isinstance(value, (list, tuple)):
        entries = value
    else:
        entries = [value]

    out: list[str] = []
    for entry in entries:
        name = _clean(entry.get(key)) if isinstance(entry, dict) else _clean(entry)
        if name and name not in out:
            out.append(name)
    return tuple(out)


def _venue_address(venue: dict[str, Any]) -> str | None:
    """The venue's postal address, comma-joined, in TEC's own field order.

    Matches what TEC's ICS export writes into ``LOCATION`` so the two Ace
    sources describe one venue the same way — which is what lets
    ``normalize.py``'s address-override heuristic and issue 0015's dedupe see
    them as the same place rather than two.
    """
    parts = [
        _clean(venue.get(key))
        for key in ("address", "city", "province", "state", "zip", "country")
    ]
    seen: list[str] = []
    for part in parts:
        if part and part not in seen:
            seen.append(part)
    return ", ".join(seen) or None


def _location(venue: dict[str, Any] | None) -> str | None:
    """The single ``LOCATION``-shaped string ``normalize.py`` consumes.

    Venue name first, then the address. Ace's venues include ``Ace Virtual
    Event`` (no address at all) and three genuinely off-site places, so neither
    half can be assumed present.
    """
    if not venue:
        return None
    name = _clean(venue.get("venue")) or _clean(venue.get("name"))
    address = _venue_address(venue)
    parts = [part for part in (name, address) if part]
    return ", ".join(parts) or None


def _status(value: Any) -> str | None:
    """TEC's WordPress post status, in ICS ``STATUS`` vocabulary.

    ``publish`` is the only status a public REST query returns, but a site that
    exposes ``canceled`` should not have it silently read as confirmed.
    """
    text = _clean(value)
    if text is None:
        return None
    lowered = text.lower()
    if lowered == "publish":
        return "CONFIRMED"
    if lowered in ("canceled", "cancelled"):
        return "CANCELLED"
    return text.upper()


def _sort_key(event: TribeEvent) -> tuple[dt.date, int, dt.time, str]:
    """Same ordering rule as the ICS adapter: day, all-day first, then time."""
    start = event.start
    if isinstance(start, dt.datetime):
        moment = start.astimezone(dt.timezone.utc) if start.tzinfo else start
        return (moment.date(), 1, moment.time(), event.uid or "")
    return (start, 0, dt.time.min, event.uid or "")


# --------------------------------------------------------------------------- the URL


def events_url(
    url: str | httpx.URL,
    *,
    start_date: dt.date | str | None = None,
    per_page: int | None = DEFAULT_PER_PAGE,
) -> httpx.URL:
    """The registry's URL with ``start_date`` and ``per_page`` filled in.

    Only ever *adds* parameters the caller left out, because ``sources.yaml``
    is authoritative: Ace's entry already carries ``params: {per_page: 50}``,
    and a source that pins ``start_date`` deliberately must keep it.

    Not a guessed URL — the path comes from the registry. See "Never guess a
    feed URL": this only supplies the two query parameters the survey used.
    """
    target = httpx.URL(str(url))
    params: dict[str, str] = {}
    if start_date is not None and "start_date" not in target.params:
        value = start_date.isoformat() if isinstance(start_date, dt.date) else str(start_date)
        params["start_date"] = value
    if per_page is not None and "per_page" not in target.params:
        params["per_page"] = str(per_page)
    return target.copy_merge_params(params) if params else target


# --------------------------------------------------------------------------- decoding


@dataclass(frozen=True)
class _PageResult:
    """Internal: one decoded page, or the reason it could not be decoded."""

    page: TribePage | None = None
    problem: TribeProblem = TribeProblem.NONE
    error: str | None = None
    #: Set when a lenient content type was accepted on a body that is real JSON.
    tolerated: str | None = None


def _decode_page(
    result: FetchResult,
    *,
    number: int,
    strict_content_type: bool = True,
) -> _PageResult:
    """Turn one :class:`FetchResult` into a :class:`TribePage` or a problem.

    Never raises. Every judgement CLAUDE.md asks for happens here, in order:
    transport, status, content type, JSON, then *the shape of the JSON* — a
    wrong adapter has to be as loud as a wrong URL, and a WordPress error object
    is perfectly valid JSON.
    """
    where = f"{result.source_key} page {number} ({result.url})"

    if result.outcome is Outcome.NOT_MODIFIED:
        return _PageResult(
            problem=TribeProblem.NOT_MODIFIED,
            error=(
                f"{where}: HTTP 304, unchanged since the last run — reuse the stored "
                "events rather than treating this as zero"
            ),
        )
    if result.outcome is not Outcome.FETCHED:
        return _PageResult(
            problem=TribeProblem.TRANSPORT,
            error=f"{where}: {result.outcome.value}"
            + (f" ({result.reason})" if result.reason else ""),
        )

    status = result.status_code
    if status is not None and not 200 <= status < 300:
        hint = ""
        if status == 404:
            hint = (
                " — on this route a 404 almost always means The Events Calendar is "
                "not installed. The Crucible is the recorded case: its pages carry "
                "tribe-* CSS class names (inert Avada theme compatibility CSS) while "
                "every wp-json/tribe/* route 404s. Class names are markup, not "
                "evidence; do not register a tribe_rest source on the strength of them"
            )
        return _PageResult(
            problem=TribeProblem.HTTP_ERROR,
            error=f"{where}: HTTP {status}{hint}",
        )

    if not result.has_body:
        return _PageResult(
            problem=TribeProblem.EMPTY_BODY,
            error=f"{where}: HTTP {status} with an empty body",
        )

    text = result.text
    tolerated: str | None = None
    if strict_content_type:
        try:
            result.expect_content_type(*JSON_CONTENT_TYPES, allow_missing=True)
        except ContentTypeMismatch as exc:
            if result.content_type_is(*LENIENT_CONTENT_TYPES) and looks_like_json_object(text):
                tolerated = result.content_type
            else:
                return _PageResult(
                    problem=TribeProblem.WRONG_CONTENT_TYPE,
                    error=(
                        f"{exc} — HTTP 200 is not success; a TEC site answering this "
                        "route with a rendered page is a source that has drifted, not "
                        "a calendar with nothing on it"
                    ),
                )

    decoded = _decode_body(text, where=where)
    if decoded.page is None:
        return decoded
    return replace(decoded, page=replace(decoded.page, url=result.url, number=number), tolerated=tolerated)


def _decode_body(text: str, *, where: str) -> _PageResult:
    """The JSON half of :func:`_decode_page`: no HTTP, only bytes."""
    if not text.strip():
        return _PageResult(problem=TribeProblem.EMPTY_BODY, error=f"{where}: empty body")

    try:
        payload = json.loads(text)
    except ValueError as exc:
        head = text.strip()[:60].replace("\n", " ")
        return _PageResult(
            problem=TribeProblem.NOT_JSON,
            error=(
                f"{where}: body is not JSON ({exc}); it starts {head!r} — the "
                "200-with-a-rendered-page case"
            ),
        )

    if not isinstance(payload, dict):
        return _PageResult(
            problem=TribeProblem.NOT_TRIBE,
            error=(
                f"{where}: JSON parsed to {type(payload).__name__}, not an object; "
                "the TEC events endpoint always returns an object with an 'events' key"
            ),
        )

    events = payload.get("events")
    if not isinstance(events, list):
        code = _clean(payload.get("code"))
        message = _clean(payload.get("message"))
        detail = f" (WordPress said {code!r}: {message})" if code else ""
        return _PageResult(
            problem=TribeProblem.NOT_TRIBE,
            error=(
                f"{where}: valid JSON with no 'events' array{detail}. Keys present: "
                f"{', '.join(sorted(map(str, payload))) or '<none>'} — this is not a "
                "The Events Calendar response, and an adapter that returned [] here "
                "would look identical to a space with no events"
            ),
        )

    items = tuple(entry for entry in events if isinstance(entry, dict))
    return _PageResult(
        page=TribePage(
            url="",
            items=items,
            total=_int(payload.get("total")),
            total_pages=_int(payload.get("total_pages")),
            next_url=_clean(payload.get("next_rest_url")),
            previous_url=_clean(payload.get("previous_rest_url")),
            rest_url=_clean(payload.get("rest_url")),
        )
    )


# --------------------------------------------------------------------------- building


def _build_event(
    item: dict[str, Any],
    *,
    page: int,
    source_url: str,
) -> TribeEvent | None:
    """One TEC event object → one :class:`TribeEvent`, or ``None`` if undated.

    An event with no usable start is dropped here rather than being handed on
    with ``start=None``: :func:`pipeline.normalize.normalize_event` would drop
    it anyway as ``NO_START``, and a half-built event in the intermediate shape
    is a trap for the next reader.
    """
    zone = _zone(item.get("timezone"))
    all_day = _bool(item.get("all_day"))

    start: dt.date | dt.datetime | None
    end: dt.date | dt.datetime | None
    tz_name: str | None = None
    source_tz: str | None = None
    form = "unknown"

    if all_day:
        # TEC writes an all-day event as 00:00:00 → 23:59:59 local, i.e. the
        # end is already the *inclusive* last day. No RFC 5545 exclusive-DTEND
        # correction applies here, unlike the ICS adapter.
        start = _parse_date(item.get("start_date"))
        end = _parse_date(item.get("end_date"))
        form = "date"
    else:
        utc_start = _parse_utc(item.get("utc_start_date"))
        utc_end = _parse_utc(item.get("utc_end_date"))
        if utc_start is not None:
            # utc_start_date is the authoritative instant. Converting it into
            # the event's own zone preserves that instant and restores the wall
            # clock the site displays, which is what start_date says.
            if zone is not None:
                start = utc_start.astimezone(zone)
                end = utc_end.astimezone(zone) if utc_end else None
                form = "tzid"
                source_tz = str(zone)
            else:
                start = utc_start
                end = utc_end
                form = "utc"
                source_tz = "UTC"
        else:
            # No utc_start_date. Fall back to the local wall clock plus the
            # declared zone; with no zone this stays naive and normalize.py's
            # floating-time policy decides what it means.
            naive_start = _parse_naive(item.get("start_date"))
            naive_end = _parse_naive(item.get("end_date"))
            if naive_start is None:
                return None
            if zone is not None:
                start = naive_start.replace(tzinfo=zone)
                end = naive_end.replace(tzinfo=zone) if naive_end else None
                form = "tzid"
                source_tz = str(zone)
            else:
                start = naive_start
                end = naive_end
                form = "floating"

    if start is None:
        return None
    if isinstance(start, dt.datetime) and start.tzinfo is not None:
        tz_name = getattr(start.tzinfo, "key", None) or str(start.tzinfo)

    days = 1
    multi_day = False
    if isinstance(start, dt.datetime) and isinstance(end, dt.datetime):
        days = max((end.date() - start.date()).days + 1, 1)
        multi_day = days > 1
    elif isinstance(start, dt.date) and isinstance(end, dt.date):
        days = max((end - start).days + 1, 1)
        multi_day = days > 1
    elif isinstance(start, dt.date):
        end = start

    venue = item.get("venue") if isinstance(item.get("venue"), dict) else None
    organizers = _names(item.get("organizer"))
    cost_details = item.get("cost_details")
    currency = (
        _clean(cost_details.get("currency_symbol"))
        if isinstance(cost_details, dict)
        else None
    )

    event_id = _int(item.get("id"))
    # global_id is TEC's own stable identity ("www.acemakerspace.org?id=12345")
    # and is what keeps the UID stable across runs. It is deliberately *not*
    # the ICS UID ("{postid}-{start}-{end}@host"): the two Ace sources are
    # meant to collide in dedupe on title and start, resolved by trust, rather
    # than to be silently identical.
    uid = _clean(item.get("global_id"))
    if not uid and event_id is not None:
        uid = f"{event_id}@{httpx.URL(source_url).host or 'tribe'}"

    return TribeEvent(
        uid=uid,
        title=_clean(item.get("title")),
        start=start,
        end=end,
        all_day=all_day,
        multi_day=multi_day,
        days=days,
        tz=tz_name,
        source_tz=source_tz,
        dtstart_form=form,
        location=_location(venue),
        description=_clean(item.get("description")) or _clean(item.get("excerpt")),
        url=_clean(item.get("url")),
        categories=_names(item.get("categories")),
        status=_status(item.get("status")),
        organizer=organizers[0] if organizers else None,
        # TEC exposes recurrence only as separate posts on the free plugin, and
        # Ace's feed carries no RRULE at all, so nothing here is expanded.
        recurring=False,
        last_modified=_parse_utc(item.get("modified_utc")),
        dtstamp=_parse_utc(item.get("date_utc")),
        event_id=event_id,
        cost=_clean(item.get("cost")),
        currency_symbol=currency,
        ticketed=_bool(item.get("ticketed")),
        is_virtual=_bool(item.get("is_virtual")),
        featured=_bool(item.get("featured")),
        venue_name=_clean(venue.get("venue")) or _clean(venue.get("name")) if venue else None,
        venue_address=_venue_address(venue) if venue else None,
        organizers=organizers,
        website=_clean(item.get("website")),
        image=_clean(item.get("image").get("url")) if isinstance(item.get("image"), dict) else None,
        page=page,
    )


def _in_window(
    event: TribeEvent, window_start: dt.date, window_end: dt.date
) -> bool:
    """Horizon clipping, on the event's own local day.

    The ICS adapter clips during recurrence expansion; there is nothing to
    expand here, so it happens explicitly. Both adapters therefore report
    *post-clip* counts, which is what issue 0016's gates count.
    """
    start = event.start
    day = start.date() if isinstance(start, dt.datetime) else start
    return window_start <= day <= window_end


def _build(
    pages: Sequence[TribePage],
    *,
    space_id: str,
    label: str,
    source_url: str,
    horizon_days: int,
    window_start: dt.date,
    window_end: dt.date,
    now: dt.datetime,
    problem: TribeProblem = TribeProblem.NONE,
    error: str | None = None,
    truncated: bool = False,
) -> TribeParse:
    """Assemble decoded pages into the returned :class:`TribeParse`."""
    events: list[TribeEvent] = []
    raw_count = 0
    newest_modified: dt.datetime | None = None
    newest_stamp: dt.datetime | None = None

    for page in pages:
        for item in page.items:
            raw_count += 1
            event = _build_event(item, page=page.number, source_url=source_url)
            if event is None:
                continue
            if event.last_modified is not None and (
                newest_modified is None or event.last_modified > newest_modified
            ):
                newest_modified = event.last_modified
            if event.dtstamp is not None and (
                newest_stamp is None or event.dtstamp > newest_stamp
            ):
                newest_stamp = event.dtstamp
            if _in_window(event, window_start, window_end):
                events.append(event)

    first = pages[0] if pages else None
    return TribeParse(
        events=tuple(sorted(events, key=_sort_key)),
        problem=problem,
        error=error,
        space_id=space_id,
        label=label,
        source_url=source_url,
        vevent_count=raw_count,
        recurring_vevent_count=0,
        last_modified=newest_modified,
        dtstamp=newest_stamp,
        horizon_days=horizon_days,
        window_start=window_start,
        window_end=window_end,
        parsed_at=now,
        reported_total=first.total if first else None,
        reported_pages=first.total_pages if first else None,
        pages=tuple(pages),
        truncated=truncated,
    )


def _failure(
    problem: TribeProblem,
    error: str,
    *,
    space_id: str = "",
    label: str = "",
    source_url: str = "",
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    window_start: dt.date | None = None,
    window_end: dt.date | None = None,
    now: dt.datetime | None = None,
    pages: Sequence[TribePage] = (),
) -> TribeParse:
    """A reported failure. Never raised — see the ``ics`` module docstring."""
    return TribeParse(
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


def parse_tribe_rest(
    result: FetchResult,
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    today: dt.date | None = None,
    now: dt.datetime | None = None,
    strict_content_type: bool = True,
    fetcher: Fetcher | None = None,
    ref: SourceRef | None = None,
    max_pages: int = MAX_PAGES,
) -> TribeParse:
    """Parse a fetched TEC REST payload, following ``next_rest_url``.

    ``result`` is page 1, already fetched by the caller (the CLI fetches every
    source's first URL itself). Pages 2..n are fetched through ``fetcher`` when
    one is supplied, which is how the nightly run gets all 92 of Ace's events
    while staying inside one rate limiter and one ``robots.txt`` decision.

    Called **without** a fetcher — a test, or a human parsing a file out of
    ``raw/`` — it parses the one page it was given and sets
    :attr:`TribeParse.truncated` if more were on offer. That is a deliberate
    single-page call, not a silent loss.

    Never raises for a bad payload: a 404, a rendered HTML page, a WordPress
    error object and a runaway ``next_rest_url`` all come back as a
    :class:`TribeParse` with ``ok=False`` and a :class:`TribeProblem`.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    today = today or now.date()
    window_start = today
    window_end = today + dt.timedelta(days=horizon_days)

    def fail(problem: TribeProblem, error: str, pages: Sequence[TribePage] = ()) -> TribeParse:
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

    first = _decode_page(result, number=1, strict_content_type=strict_content_type)
    if first.page is None:
        return fail(first.problem, first.error or "page 1 could not be decoded")

    pages: list[TribePage] = [first.page]
    notes: list[str] = []
    if first.tolerated:
        notes.append(f"content type {first.tolerated!r} tolerated: the body is real JSON")

    truncated = False
    next_url = first.page.next_url

    if next_url and (fetcher is None or ref is None):
        # A single-page call. Say so loudly in the result rather than returning
        # 50 of 92 events as though that were the whole calendar.
        truncated = True
        of_total = f" of {first.page.total}" if first.page.total is not None else ""
        notes.append(
            f"next_rest_url present ({next_url}) but no fetcher was supplied, so only "
            f"page 1 was read: {len(first.page)}{of_total} events"
        )
        next_url = None

    seen: set[str] = {str(result.url), str(first.page.rest_url or "")}
    number = 1
    while next_url:
        if number >= max_pages:
            # A server that always offers another page. Everything collected so
            # far is suspect, so this is a reported failure and not a quietly
            # truncated list — see the module docstring.
            return _build(
                pages,
                space_id=result.space_id,
                label=result.label,
                source_url=result.url,
                horizon_days=horizon_days,
                window_start=window_start,
                window_end=window_end,
                now=now,
                problem=TribeProblem.RUNAWAY_PAGINATION,
                error=(
                    f"{result.source_key}: still offering next_rest_url after "
                    f"{max_pages} pages ({next_url}). Refusing to keep walking a feed "
                    "that never ends; the counts collected so far cannot be trusted"
                ),
            )
        if next_url in seen:
            return _build(
                pages,
                space_id=result.space_id,
                label=result.label,
                source_url=result.url,
                horizon_days=horizon_days,
                window_start=window_start,
                window_end=window_end,
                now=now,
                problem=TribeProblem.RUNAWAY_PAGINATION,
                error=(
                    f"{result.source_key}: next_rest_url points back at a page already "
                    f"read ({next_url}) after {len(pages)} pages — a pagination loop"
                ),
            )

        seen.add(next_url)
        number += 1
        assert fetcher is not None and ref is not None  # narrowed above
        page_result = fetcher.fetch_url(ref, next_url, label_suffix=f"p{number}")
        decoded = _decode_page(
            page_result, number=number, strict_content_type=strict_content_type
        )
        if decoded.page is None:
            # Page 1 was fine and a later page was not. Failing the whole parse
            # hands the night to carry-forward, which republishes yesterday's
            # complete set — strictly better than publishing a partial one that
            # looks healthy.
            return _build(
                pages,
                space_id=result.space_id,
                label=result.label,
                source_url=result.url,
                horizon_days=horizon_days,
                window_start=window_start,
                window_end=window_end,
                now=now,
                problem=TribeProblem.PAGE_FAILED,
                error=(
                    f"pagination stopped at page {number} of "
                    f"{first.page.total_pages or '?'}: {decoded.error} "
                    f"(problem={decoded.problem.value}; {sum(len(p) for p in pages)} "
                    "events collected before the failure, deliberately not published "
                    "as if they were the whole feed)"
                ),
            )

        page = decoded.page
        pages.append(page)
        if not page.items:
            # A 200 with an empty list is the end of the feed, not an error.
            # A site whose event count lands exactly on the page boundary
            # answers this way.
            break
        next_url = page.next_url

    parse = _build(
        pages,
        space_id=result.space_id,
        label=result.label,
        source_url=result.url,
        horizon_days=horizon_days,
        window_start=window_start,
        window_end=window_end,
        now=now,
        truncated=truncated,
    )
    if notes:
        return replace(parse, error="; ".join(notes))
    return parse


def parse_tribe_rest_text(
    text: str,
    *,
    space_id: str = "",
    label: str = "",
    source_url: str = "",
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    today: dt.date | None = None,
    now: dt.datetime | None = None,
) -> TribeParse:
    """Parse **one page** of TEC JSON text. The half with no HTTP in it.

    Split out for the same reason ``ics`` splits ``parse_ics_text``: the tests,
    and a human diffing two files out of ``raw/``, must be able to reach the
    parser without manufacturing a :class:`~pipeline.fetch.FetchResult`. It
    never paginates — there is no fetcher to paginate with.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    today = today or now.date()
    window_start = today
    window_end = today + dt.timedelta(days=horizon_days)

    decoded = _decode_body(text, where=f"{space_id}:{label}")
    if decoded.page is None:
        return _failure(
            decoded.problem,
            decoded.error or "body could not be decoded",
            space_id=space_id,
            label=label,
            source_url=source_url,
            horizon_days=horizon_days,
            window_start=window_start,
            window_end=window_end,
            now=now,
        )

    page = replace(decoded.page, url=source_url, number=1)
    parse = _build(
        [page],
        space_id=space_id,
        label=label,
        source_url=source_url,
        horizon_days=horizon_days,
        window_start=window_start,
        window_end=window_end,
        now=now,
        truncated=bool(page.next_url),
    )
    if page.next_url:
        return replace(
            parse,
            error=(
                f"next_rest_url present ({page.next_url}); parse_tribe_rest_text reads "
                "one page only"
            ),
        )
    return parse


# --------------------------------------------------------------------------- fetching


def fetch_tribe_rest(
    fetcher: Fetcher,
    ref: SourceRef,
    *,
    horizon_days: int | None = None,
    today: dt.date | None = None,
    now: dt.datetime | None = None,
    start_date: dt.date | None = None,
    per_page: int | None = DEFAULT_PER_PAGE,
    conditional: bool = False,
    strict_content_type: bool = True,
    max_pages: int = MAX_PAGES,
) -> TribeParse:
    """Fetch a registered source's first page and walk the rest. All in one call.

    The counterpart to :func:`pipeline.adapters.gcal_ics.fetch_gcal_ics`, and
    the entry point for the repair workflow: one source, every page, no run.

    ``conditional`` defaults to **off**. A 304 on page 1 would leave nothing to
    read ``next_rest_url`` from, and this endpoint is a few hundred kilobytes
    rather than Maker Nexus's 11 MB, so the saving is not worth the special
    case. The nightly run reaches this adapter through
    :func:`pipeline.cli.process_source`, which applies the registry's own
    conditional-GET policy to page 1 and hands the result to
    :func:`parse_tribe_rest`.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    today = today or now.date()
    if horizon_days is None:
        horizon_days = fetcher.registry.horizon_days

    _space, source = ref
    url = events_url(
        source.url or "",
        start_date=start_date if start_date is not None else today,
        per_page=per_page,
    )
    result = fetcher.fetch_url(ref, url, conditional=conditional)
    return parse_tribe_rest(
        result,
        horizon_days=horizon_days,
        today=today,
        now=now,
        strict_content_type=strict_content_type,
        fetcher=fetcher,
        ref=ref,
        max_pages=max_pages,
    )


#: The adapter entry point named by ``sources.yaml``'s ``adapter: tribe_rest``.
parse = parse_tribe_rest


__all__ = [
    "DEFAULT_PER_PAGE",
    "LENIENT_CONTENT_TYPES",
    "MAX_PAGES",
    "TRIBE_EVENTS_PATH",
    "TribeEvent",
    "TribePage",
    "TribeParse",
    "TribeProblem",
    "events_url",
    "fetch_tribe_rest",
    "looks_like_json_object",
    "parse",
    "parse_tribe_rest",
    "parse_tribe_rest_text",
]
