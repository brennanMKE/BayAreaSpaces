"""Bookwhen's server-rendered public agenda table. **No browser, no model.**

Bookwhen hosts the booking calendar for a lot of small spaces, and its public
page — ``https://bookwhen.com/{account}`` — ships the whole agenda as ordinary
server-rendered HTML. Every event is one ``<tr data-hook="agenda_list_item">``
and, crucially, that row carries::

    data-event="ev-{entryid}-{YYYYMMDDHHMMSS}"

**That attribute is the entire reason this adapter is deterministic.** The
14-digit stamp is the event's exact local start, written by the server, so
nothing here has to read "Thu 13 Aug, 6:00 PM – 8:00 PM PDT" out of a cell and
guess what year it meant. Title comes from the row's ``<button>``; the times are
``America/Los_Angeles`` for the one registered consumer. Roughly twenty lines of
real parsing, and it generalizes to any Bookwhen-hosted space.

Status: this is a **fallback**, and it should stay labelled as one
------------------------------------------------------------------

Issue 0002 is asking Sequoia Fabrica for the token on their Bookwhen ICS feed
(``webcal://feeds.bookwhen.com/ical/lcqebfpp6u7h/{TOKEN}/public.ics``). That feed
is strictly better than this: it is iCalendar, it carries real ``DTEND`` and
``LAST-MODIFIED`` values, and it does not depend on Bookwhen's markup holding
still. **When the token lands, demote this source or drop it** — the registry
already has the ICS entry sitting at ``trust: 100`` with ``url: TODO`` waiting
for it, and this one at ``trust: 95``.

It is worth building anyway, today, because it is small and because it unblocks
the space's primary public calendar — 20 real events, 2026-08-13 → 2027-01-05 —
without waiting on a human to paste a string.

Why not ``llm_html``
--------------------

Deliberate. ``llm_html`` exists for the Noisebridge wiki, where the schedule is
prose. Here the markup is regular: a stable ``data-hook``, a machine-written
timestamp in an attribute, and a title in a single element. A small parser is
cheaper than a model, cannot hallucinate a date, and works for the next Bookwhen
space without a prompt. CLAUDE.md's standing rule applies — *treat every new
``llm_html`` entry as a small failure to find the real feed* — and this is what
looking harder produced.

The dead end someone will find, and why it is a dead end
--------------------------------------------------------

``events.sequoiafabrica.org`` looks exactly like a real events site, and it is
referenced from the space's own wiki. It is a **catch-all redirect**. Verified
2026-08-05: ``/events.ics``, ``/calendar.ics``, ``/ical``, ``/feed``,
``/feed.xml``, ``/rss``, ``/index.xml``, ``/robots.txt`` and ``/sitemap.xml``
*all* answer 200 with the identical 67,502-byte Bookwhen page. If you register
``events.sequoiafabrica.org/events.ics`` as an ``ics`` source it will fetch
happily, parse to nothing, and report an empty calendar forever. That is the
worst failure this project has, delivered by a URL that looks right.

What is reported rather than returned empty
-------------------------------------------

Every judgement is a named :class:`BookwhenProblem`, because "0 events" from
each of them means something different at 09:00:

- **Not a Bookwhen agenda page at all** (:attr:`BookwhenProblem.NOT_BOOKWHEN`) —
  a 200 carrying somebody's error page, a login wall, or the redirect above.
  This is the check that keeps an unrelated HTML body from being read as an
  empty calendar.
- **A Bookwhen page with no agenda rows and no empty-state marker**
  (:attr:`BookwhenProblem.NO_ROWS`) — the markup drifted, or the rows moved
  behind JavaScript. A repair, not a quiet week.
- **A Bookwhen page that explicitly says it has nothing on**
  (:attr:`BookwhenParse.reported_empty`) — ``ok``, zero events, and a human can
  trust it. Issue 0016's ``allow_zero`` takes it from there.
- **Rows present, not one with a usable ``data-event``**
  (:attr:`BookwhenProblem.NO_DATES`) — the attribute was renamed or reformatted.
  A *single* malformed row never costs the others: it is skipped and counted
  (:attr:`BookwhenParse.bad_row_count`).

Pagination is a deliberate second step, not the default path
-------------------------------------------------------------

The public page returns **20 rows**. Sequoia Fabrica has exactly 20 events and
they already run to 2027-01-05, well past the 120-day horizon, so one request is
the whole calendar and :func:`fetch_bookwhen_html` makes exactly one by default.

If a busier account ever fills those 20 rows *before* the horizon ends, the
"Show more…" button calls ``/{account}/calendar_items?offset=…&limit=…`` — 200,
``text/javascript``, and **it is not JSON**. It answers with jQuery statements
wrapping escaped HTML, so it has to be unescaped and then parsed as markup;
:func:`html_from_calendar_items` does that and :func:`fetch_bookwhen_html`
accepts ``paginate=True``. It stays opt-in because an endpoint that returns
executable JavaScript is a thing to touch on purpose, once, with a reason —
never as the default path for every source.

Implemented by issue 0024.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import lxml.etree
from extruct.utils import parse_html

from pipeline.adapters.embedded_json import (
    DEFAULT_ZONE,
    LENIENT_CONTENT_TYPES,
    looks_like_html,
)

# One decoder for text, one for offsets, shared rather than re-implemented: two
# adapters reading the same space must not disagree about what ``&amp;`` or
# ``-07:00`` looks like.
from pipeline.adapters.embedded_json import (  # noqa: PLC2701
    _clean as clean,
)
from pipeline.adapters.ics import DEFAULT_HORIZON_DAYS, IcsEvent, IcsParse
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

#: The row. Bookwhen's own test hook, which is why it is a better anchor than a
#: CSS class: hooks exist so *their* tests can find these rows, so they change
#: less often than presentation does.
AGENDA_ROW_HOOK = "agenda_list_item"

#: ``ev-{entryid}-{YYYYMMDDHHMMSS}``. The stamp is anchored to the end and the
#: entry id is allowed to contain hyphens, because it is opaque and we do not
#: get to decide what is in it. Greedy ``.+`` plus the anchored 14 digits means
#: the *last* dash-delimited 14-digit group is the stamp, which is the only
#: reading that survives an id with a hyphen in it.
DATA_EVENT_RE = re.compile(r"^ev-(?P<entry>.+)-(?P<stamp>\d{14})$")

#: How the stamp is written.
STAMP_FORMAT = "%Y%m%d%H%M%S"

#: Rows the public page returns without pagination. Observed, not configured:
#: exactly 20 on 2026-08-05.
DEFAULT_PAGE_SIZE = 20

#: The "Show more…" endpoint, relative to the account. Returns escaped HTML
#: inside jQuery statements — see :func:`html_from_calendar_items`.
CALENDAR_ITEMS_PATH = "calendar_items"

#: ``data-hook`` values that mean "this really is a Bookwhen agenda". Any hook
#: starting with ``agenda`` counts; these are named for the diagnostic string.
AGENDA_HOOK_PREFIX = "agenda"

#: ``data-hook`` values that mean "the agenda rendered and there is nothing on".
#: **Never observed live** — Sequoia Fabrica's page has not been empty since we
#: started looking — so this is the honest half of a guess: if one of these is
#: present the zero is believed, and if none is present a page with no rows is
#: reported as a problem rather than published as an empty calendar. The
#: conservative direction is the one that cannot silently delete a space.
EMPTY_HOOKS: tuple[str, ...] = (
    "agenda_list_empty",
    "agenda_empty",
    "agenda_list_no_events",
    "no_events",
)

#: Rows, anywhere in the document. ``descendant-or-self`` so a fragment parsed
#: out of ``calendar_items`` (which is a bare run of ``<tr>``s) matches too.
_ROW_XPATH = lxml.etree.XPath(
    f'descendant-or-self::*[@data-hook="{AGENDA_ROW_HOOK}"]'
)
_HOOKED_XPATH = lxml.etree.XPath("descendant-or-self::*[@data-hook]")
_OPTIONS_XPATH = lxml.etree.XPath("descendant-or-self::*[@data-options]")
_BUTTON_XPATH = lxml.etree.XPath("descendant::button")
_LINK_XPATH = lxml.etree.XPath("descendant::a[@href]")


def _class_xpath(name: str) -> lxml.etree.XPath:
    """An XPath matching one whole class token, not a substring.

    ``contains(@class, "location")`` also matches ``location-hidden``; the
    space-padded form is the standard fix and it matters here because these
    cells are read for display text.
    """
    return lxml.etree.XPath(
        "descendant::*[contains(concat(' ', normalize-space(@class), ' '), "
        f"' {name} ')]"
    )


_DURATION_XPATH = _class_xpath("duration")
_LOCATION_XPATH = _class_xpath("location")

#: Double-quoted JavaScript string literals, escapes included. Used only to
#: unpick the ``calendar_items`` response.
_JS_STRING_RE = re.compile(r'"(?:[^"\\]|\\.)*"')


# --------------------------------------------------------------------------- outcomes


class BookwhenProblem(str, Enum):
    """Why a :class:`BookwhenParse` is not usable. ``NONE`` means it is.

    The first five mirror :class:`~pipeline.adapters.ics.IcsProblem` so the
    CLI's ``record.problem`` stays one vocabulary; the rest are the failures
    only an agenda-table parse can have.
    """

    NONE = "none"
    TRANSPORT = "transport"
    NOT_MODIFIED = "not_modified"
    EMPTY_BODY = "empty_body"
    WRONG_CONTENT_TYPE = "wrong_content_type"

    #: Non-2xx on the registered URL.
    HTTP_ERROR = "http_error"
    #: HTML, 200, and **not a Bookwhen agenda page**: no ``agenda*`` hook and no
    #: ``data-options`` calendar block anywhere in it. A login wall, an error
    #: page, or a redirect that landed somewhere else. This is the check that
    #: stops an unrelated body being read as a calendar with nothing in it.
    NOT_BOOKWHEN = "not_bookwhen"
    #: A Bookwhen page carrying **no** ``agenda_list_item`` rows and no
    #: empty-state marker. The markup drifted or the rows went behind
    #: JavaScript; either way it wants a human, not an empty publish.
    NO_ROWS = "no_rows"
    #: Rows were found and **not one** carried a parseable ``data-event``. The
    #: attribute was renamed or reformatted — a schema change, not a quiet week.
    NO_DATES = "no_dates"
    #: The body could not be parsed as a document at all.
    UNPARSEABLE = "unparseable"


# --------------------------------------------------------------------------- rows


@dataclass(frozen=True)
class AgendaRow:
    """One ``<tr data-hook="agenda_list_item">``, as read off the page.

    Deliberately pre-timezone: :attr:`start_local` is the **naive** wall clock
    the server wrote. Nothing naive leaves this module — :func:`_build_events`
    attaches the zone — but keeping the raw form here is what lets a test assert
    that the attribute was decoded correctly, separately from what we did with
    it afterwards.
    """

    #: The ``data-event`` attribute verbatim, malformed or not. Provenance.
    data_event: str
    entry_id: str | None = None
    stamp: str | None = None
    start_local: dt.datetime | None = None
    title: str | None = None
    #: ``td.duration`` as written: ``"6:00 PM – 8:00 PM PDT"``. **Never parsed
    #: into a ``DTEND``** — see :class:`BookwhenEvent`.
    time_text: str | None = None
    location: str | None = None
    url: str | None = None

    @property
    def usable(self) -> bool:
        return self.entry_id is not None and self.start_local is not None


def parse_data_event(value: str | None) -> tuple[str, dt.datetime] | None:
    """``"ev-sf3k2-20260813180000"`` → ``("sf3k2", 2026-08-13 18:00 naive)``.

    ``None`` for anything that does not match, which the caller counts as a bad
    row rather than dropping in silence. The returned datetime is naive on
    purpose: this function decodes an attribute, it does not decide a timezone.
    """
    text = (value or "").strip()
    match = DATA_EVENT_RE.match(text)
    if match is None:
        return None
    try:
        start = dt.datetime.strptime(match.group("stamp"), STAMP_FORMAT)
    except ValueError:
        # 14 digits that are not a date: "ev-x-99999999999999".
        return None
    return match.group("entry"), start


def _text(nodes: Sequence[Any]) -> str | None:
    """The first node's text content, cleaned and HTML-unescaped."""
    for node in nodes:
        text = clean(str(node.xpath("string()")))
        if text:
            return text
    return None


def _row(node: Any, *, base_url: str = "") -> AgendaRow:
    """One row element → an :class:`AgendaRow`."""
    data_event = (node.get("data-event") or "").strip()
    decoded = parse_data_event(data_event)
    href = next((str(link.get("href")) for link in _LINK_XPATH(node)), None)
    return AgendaRow(
        data_event=data_event,
        entry_id=decoded[0] if decoded else None,
        stamp=data_event[-14:] if decoded else None,
        start_local=decoded[1] if decoded else None,
        # The title is the row's <button>. Bookwhen renders the whole row as a
        # button so it can open the booking panel, which is why this is one
        # element and not a text scrape across cells.
        title=_text(_BUTTON_XPATH(node)),
        time_text=_text(_DURATION_XPATH(node)),
        location=_text(_LOCATION_XPATH(node)),
        url=urljoin(base_url, href) if href else None,
    )


def agenda_rows(document: str, *, base_url: str = "") -> tuple[AgendaRow, ...]:
    """Every agenda row in one document (or fragment), in page order.

    Uses ``extruct``'s HTML parser so this module, ``jsonld`` and
    ``embedded_json`` all see the same tree, and so a fragment of bare ``<tr>``
    elements out of ``calendar_items`` is still parsed rather than rejected.
    """
    tree = parse_html(document, "UTF-8")
    return tuple(_row(node, base_url=base_url) for node in _ROW_XPATH(tree))


def data_hooks(document: str) -> tuple[str, ...]:
    """Every ``data-hook`` value on the page, deduplicated, in document order.

    The diagnostic that turns "no rows" into "no rows, and the hooks present are
    ``login_form`` and ``error_page``".
    """
    tree = parse_html(document, "UTF-8")
    out: list[str] = []
    for node in _HOOKED_XPATH(tree):
        value = (node.get("data-hook") or "").strip()
        if value and value not in out:
            out.append(value)
    return tuple(out)


def looks_like_bookwhen(document: str) -> bool:
    """True when this document is a Bookwhen agenda page.

    Two independent signals, either of which is enough: any ``data-hook``
    beginning ``agenda``, or a ``data-options`` attribute carrying a
    ``calendar`` key (the blob the account id is read from). Both are Bookwhen's
    own markup rather than branding, so a page that merely *mentions* Bookwhen —
    the space's own site links to it — does not qualify.
    """
    tree = parse_html(document, "UTF-8")
    for node in _HOOKED_XPATH(tree):
        if (node.get("data-hook") or "").strip().startswith(AGENDA_HOOK_PREFIX):
            return True
    return account_options(document) is not None


def empty_markers(document: str) -> tuple[str, ...]:
    """The empty-state hooks present, if any. See :data:`EMPTY_HOOKS`."""
    hooks = data_hooks(document)
    return tuple(hook for hook in hooks if hook in EMPTY_HOOKS)


# --------------------------------------------------------------------------- account


def account_options(document: str) -> dict[str, Any] | None:
    """The first ``data-options`` blob carrying a ``calendar`` key.

    ``{"calendar": "lcqebfpp6u7h", …}`` on Sequoia Fabrica's page — the internal
    account id the ``calendar_items`` endpoint wants. Returns ``None`` rather
    than raising when the attribute is absent or is not JSON.
    """
    tree = parse_html(document, "UTF-8")
    for node in _OPTIONS_XPATH(tree):
        raw = node.get("data-options") or ""
        try:
            payload = json.loads(raw)
        except ValueError:
            continue
        if isinstance(payload, dict) and payload.get("calendar"):
            return payload
    return None


def account_id(document: str) -> str | None:
    """The internal calendar id, e.g. ``"lcqebfpp6u7h"``."""
    options = account_options(document)
    return clean(options.get("calendar")) if options else None


def account_slug(url: str) -> str | None:
    """The account path segment: ``https://bookwhen.com/sequoiafabrica`` → ``"sequoiafabrica"``.

    The *path* segment, which is not the same string as :func:`account_id` —
    ``calendar_items`` lives under the slug and takes the id as a parameter.
    """
    parts = [part for part in urlparse(url).path.split("/") if part]
    return parts[0] if parts else None


def calendar_items_url(
    page_url: str,
    *,
    offset: int = DEFAULT_PAGE_SIZE,
    limit: int = 50,
    calendar: str | None = None,
) -> str | None:
    """The "Show more…" URL for a page, or ``None`` if the slug is unreadable.

    Built from the page's own URL and its own ``data-options`` id — nothing is
    guessed. See the module docstring for why calling it is opt-in.
    """
    slug = account_slug(page_url)
    if not slug:
        return None
    base = f"{urlparse(page_url).scheme or 'https'}://{urlparse(page_url).netloc}"
    query = f"offset={offset}&limit={limit}"
    if calendar:
        query += f"&calendar={calendar}"
    return f"{base}/{slug}/{CALENDAR_ITEMS_PATH}?{query}&context=api"


def html_from_calendar_items(text: str) -> str:
    """Unpick the ``calendar_items`` response into HTML.

    **It is not JSON**, despite ``context=api``. It answers 200 with
    ``text/javascript`` carrying jQuery statements whose arguments are escaped
    HTML::

        $("#agenda_list").append("<tr data-hook=\\"agenda_list_item\\" …>…");

    So: pull every double-quoted JavaScript string literal, decode the ones that
    mention the row hook, and hand the result back as markup. Decoding is
    :func:`json.loads` on the literal — JSON string escapes and JavaScript's are
    the same for everything this endpoint emits, and using a real decoder beats
    a chain of ``str.replace`` calls that would get ``\\u003c`` wrong.
    """
    parts: list[str] = []
    for literal in _JS_STRING_RE.findall(text):
        try:
            decoded = json.loads(literal)
        except ValueError:
            continue
        if isinstance(decoded, str) and AGENDA_ROW_HOOK in decoded:
            parts.append(decoded)
    return "".join(parts)


# --------------------------------------------------------------------------- events


@dataclass(frozen=True)
class BookwhenEvent(IcsEvent):
    """One agenda row, in the intermediate shape the ``ics`` adapter produces.

    A subclass rather than a parallel class, for the reason every adapter here
    subclasses it: ``normalize.py`` consumes every adapter through
    :func:`~pipeline.normalize.from_ics_event`, and the moment a second shape
    appears one of them starts quietly losing fields.
    """

    #: The ``{entryid}`` out of ``data-event``. Bookwhen's own stable key for
    #: the entry, and the first half of the UID.
    entry_id: str | None = None
    #: The 14-digit local start, verbatim. The second half of the UID.
    start_stamp: str | None = None

    #: ``td.duration`` as written — ``"6:00 PM – 8:00 PM PDT"``, or sometimes a
    #: bare ``"2 hours"``. **Carried, never parsed.** :attr:`end` stays ``None``
    #: because the only end time on this page is inside a display string whose
    #: format we have seen exactly one variant of; ``DTSTART + a guess`` renders
    #: a wrong block in every calendar client. The ICS feed issue 0002 is
    #: chasing carries a real ``DTEND``, which is one more reason to prefer it.
    time_text: str | None = None

    #: 0 for the default page, 1+ for anything that came out of
    #: ``calendar_items``. Provenance: a row nobody would have seen without
    #: pagination should be identifiable as such.
    page: int = 0


@dataclass
class _RowTally:
    """Internal: what the row walk saw, so the counts are not recomputed."""

    rows: int = 0
    bad: int = 0
    bad_values: list[str] = field(default_factory=list)
    duplicates: int = 0
    untitled: int = 0
    events: list[BookwhenEvent] = field(default_factory=list)


def _zone(name: str = DEFAULT_ZONE) -> ZoneInfo | None:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):  # pragma: no cover
        return None


def _build_events(
    rows: Iterable[tuple[int, AgendaRow]],
    *,
    zone: ZoneInfo | None,
    zone_name: str,
    tally: _RowTally,
) -> None:
    """Rows → events, one per row, appended to ``tally``.

    The timezone is attached here and only here. Bookwhen writes a **wall
    clock**, so the venue's zone is the correct reading of it, and
    :attr:`~pipeline.adapters.ics.IcsEvent.tz` carries a resolvable IANA name
    rather than an offset because :func:`pipeline.normalize.day_start_utc` and
    issue 0016's health filter both call ``ZoneInfo`` on it — and that filter
    runs *outside* the per-source try/except.

    On the two hours a year when a wall clock is ambiguous or does not exist,
    ``ZoneInfo`` resolves with ``fold=0``. That is a deterministic reading of an
    inherently ambiguous string and it is the same one every other consumer of
    this page gets; nothing better is available from a wall clock alone.
    """
    seen: set[str] = set()
    for page, row in rows:
        tally.rows += 1
        if not row.usable:
            # One unreadable row must never cost the other nineteen.
            tally.bad += 1
            if row.data_event:
                tally.bad_values.append(row.data_event)
            continue
        if row.data_event in seen:
            # A paginated window overlapping the default page. Counted, because
            # a source that suddenly returns each event twice is worth seeing.
            tally.duplicates += 1
            continue
        seen.add(row.data_event)
        if row.title is None:
            tally.untitled += 1

        start = row.start_local
        assert start is not None  # row.usable
        aware = start.replace(tzinfo=zone) if zone is not None else start

        tally.events.append(
            BookwhenEvent(
                # {entryid}:{stamp}. The entry id is Bookwhen's own key and the
                # stamp is what the server wrote, so this is stable across runs
                # by construction and across a re-parse of the same bytes.
                # normalize.py namespaces it to {space_id}:{entryid}:{stamp}.
                #
                # The stamp is used rather than an epoch on purpose: it is the
                # source's own text, so the UID cannot move if our timezone
                # handling ever changes. A recurring series here is a series of
                # separate entries with separate ids, so `recurring` stays False
                # and the occurrence start is already in the key.
                uid=f"{row.entry_id}:{row.stamp}",
                title=row.title,
                start=aware,
                end=None,
                all_day=False,
                multi_day=False,
                days=1,
                tz=zone_name if zone is not None else None,
                source_tz=offset_text(aware) if zone is not None else None,
                dtstart_form="tzid",
                # Sequoia Fabrica's rows carry no location cell at all, so this
                # is None for every observed event and VenuePolicy applies the
                # space's address_override — exactly as it does for The
                # Crucible's catalog. The cell is read when a Bookwhen account
                # does render one (they are room names, not addresses), because
                # a named room is evidence VenuePolicy can act on.
                location=row.location,
                description=None,
                url=row.url,
                categories=(),
                status=None,
                organizer=None,
                recurring=False,
                last_modified=None,
                dtstamp=None,
                entry_id=row.entry_id,
                start_stamp=row.stamp,
                time_text=row.time_text,
                page=page,
            )
        )


# --------------------------------------------------------------------------- result


@dataclass(frozen=True)
class BookwhenParse(IcsParse):
    """The adapter's return value. Same surface as :class:`IcsParse`, plus the page.

    Subclasses :class:`~pipeline.adapters.ics.IcsParse` so every consumer
    written against the ICS adapter — ``normalize_ics``, the CLI's
    ``SourceRecord``, issue 0016's gates — works unchanged.
    """

    problem: BookwhenProblem = BookwhenProblem.NONE  # type: ignore[assignment]

    #: ``data-hook`` values seen anywhere on the page, in document order. What
    #: turns "no rows" into a one-line diagnosis.
    hooks_seen: tuple[str, ...] = ()
    #: The empty-state hooks that were present, if any.
    empty_hooks_seen: tuple[str, ...] = ()
    #: The internal calendar id out of ``data-options``: ``"lcqebfpp6u7h"``.
    calendar_id: str | None = None
    #: ``sha1(document)[:16]``, for diffing two files out of ``raw/``.
    page_digest: str = ""
    page_bytes: int = 0

    #: ``<tr data-hook="agenda_list_item">`` elements seen, across every
    #: document read. 20 on the default page.
    row_count: int = 0
    #: Rows whose ``data-event`` did not decode. Counted, never silent.
    bad_row_count: int = 0
    #: The offending attribute values, capped, for the log.
    bad_row_values: tuple[str, ...] = ()
    #: Rows repeating a ``data-event`` already seen — a paginated window
    #: overlapping the default page.
    duplicate_row_count: int = 0
    #: Rows with no ``<button>`` text. They still publish; normalize decides.
    untitled_row_count: int = 0
    #: Documents read. 1 unless pagination was asked for.
    page_count: int = 1
    #: Local day of the **last row on the page**, before horizon clipping. The
    #: only number that answers "did this page reach the end of the window?" —
    #: :attr:`events` cannot, because clipping guarantees its last entry is
    #: inside the window whether the page ran out of events or not.
    last_row_start: dt.date | None = None
    #: True when the page carried an explicit empty-state marker. **The
    #: difference between "nothing on" and "something broke".**
    reported_empty: bool = False

    @property
    def ok(self) -> bool:
        """True when the page was a Bookwhen agenda and we read it.

        Zero events with ``ok=True`` happens two ways: the page declared itself
        empty (:attr:`reported_empty`), or every row fell outside the horizon.
        Both are ``allow_zero``'s problem, not a parse failure.
        """
        return self.problem is BookwhenProblem.NONE

    @property
    def looks_paginated(self) -> bool:
        """The default window may be short of the horizon.

        True when the page returned a full :data:`DEFAULT_PAGE_SIZE` of rows and
        the last of them — **before** horizon clipping — still lands inside the
        window, i.e. exactly the condition under which ``calendar_items`` is
        worth a request. False for Sequoia Fabrica, whose 20 rows run to
        2027-01-05, a month past a 120-day window opened on 2026-08-05.

        Reading the clipped :attr:`events` here instead would be a bug that
        always said yes: clipping guarantees the last surviving event is inside
        the window.
        """
        if self.page_count > 1 or self.row_count < DEFAULT_PAGE_SIZE:
            return False
        if self.last_row_start is None or self.window_end is None:
            return False
        return self.last_row_start < self.window_end

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        head = f"{self.space_id}:{self.label}" if self.space_id or self.label else "<bookwhen_html>"
        if not self.ok:
            return f"{head} FAILED {self.problem.value}: {self.error}"
        if self.reported_empty:
            return f"{head} 0 events, and the agenda renders its empty state"
        return (
            f"{head} {self.event_count} events in {self.horizon_days}d "
            f"(from {self.row_count} agenda rows across {self.page_count} page(s))"
        )


# --------------------------------------------------------------------------- building


def _sort_key(event: BookwhenEvent) -> tuple[dt.date, int, dt.time, str]:
    """Same ordering rule as the ICS adapter: day, all-day first, then time."""
    start = event.start
    if isinstance(start, dt.datetime):
        moment = start.astimezone(dt.timezone.utc) if start.tzinfo else start
        return (moment.date(), 1, moment.time(), event.uid or "")
    return (start, 0, dt.time.min, event.uid or "")


def _build(
    tally: _RowTally,
    *,
    hooks_seen: Sequence[str],
    empty_hooks_seen: Sequence[str],
    calendar_id: str | None,
    page_digest: str,
    page_bytes: int,
    page_count: int,
    reported_empty: bool,
    space_id: str,
    label: str,
    source_url: str,
    horizon_days: int,
    window_start: dt.date,
    window_end: dt.date,
    now: dt.datetime,
    error: str | None = None,
) -> BookwhenParse:
    """Assemble the walk into the returned :class:`BookwhenParse`.

    Horizon clipping happens here, on the event's own local day, so the count
    this reports is the post-clip one issue 0016's gates read.
    """
    days = [
        event.start.date() if isinstance(event.start, dt.datetime) else event.start
        for event in tally.events
    ]
    kept = [
        event
        for event, day in zip(tally.events, days, strict=True)
        if window_start <= day <= window_end
    ]

    return BookwhenParse(
        events=tuple(sorted(kept, key=_sort_key)),
        problem=BookwhenProblem.NONE,
        error=error,
        space_id=space_id,
        label=label,
        source_url=source_url,
        # Raw = agenda rows seen. The health gates count `event_count`, which is
        # the post-clip number.
        vevent_count=tally.rows,
        recurring_vevent_count=0,
        horizon_days=horizon_days,
        window_start=window_start,
        window_end=window_end,
        parsed_at=now,
        hooks_seen=tuple(hooks_seen),
        empty_hooks_seen=tuple(empty_hooks_seen),
        calendar_id=calendar_id,
        page_digest=page_digest,
        page_bytes=page_bytes,
        row_count=tally.rows,
        bad_row_count=tally.bad,
        bad_row_values=tuple(tally.bad_values[:10]),
        duplicate_row_count=tally.duplicates,
        untitled_row_count=tally.untitled,
        page_count=page_count,
        last_row_start=max(days) if days else None,
        reported_empty=reported_empty,
    )


def _failure(
    problem: BookwhenProblem,
    error: str,
    *,
    space_id: str = "",
    label: str = "",
    source_url: str = "",
    hooks_seen: Sequence[str] = (),
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    window_start: dt.date | None = None,
    window_end: dt.date | None = None,
    now: dt.datetime | None = None,
    page_bytes: int = 0,
    page_digest: str = "",
    calendar_id: str | None = None,
    row_count: int = 0,
    bad_row_count: int = 0,
    bad_row_values: Sequence[str] = (),
) -> BookwhenParse:
    """A reported failure. Never raised — see the ``ics`` module docstring."""
    return BookwhenParse(
        problem=problem,
        error=error,
        space_id=space_id,
        label=label,
        source_url=source_url,
        hooks_seen=tuple(hooks_seen),
        horizon_days=horizon_days,
        window_start=window_start,
        window_end=window_end,
        parsed_at=now,
        page_bytes=page_bytes,
        page_digest=page_digest,
        calendar_id=calendar_id,
        row_count=row_count,
        bad_row_count=bad_row_count,
        bad_row_values=tuple(bad_row_values[:10]),
    )


# --------------------------------------------------------------------------- decoding


def _transport_problem(result: FetchResult, *, where: str) -> tuple[BookwhenProblem, str] | None:
    """Everything that can be wrong before the body is worth looking at."""
    if result.outcome is Outcome.NOT_MODIFIED:
        return (
            BookwhenProblem.NOT_MODIFIED,
            f"{where}: HTTP 304, unchanged since the last run — reuse the stored "
            "events rather than treating this as zero",
        )
    if result.outcome is not Outcome.FETCHED:
        detail = f" ({result.reason})" if result.reason else ""
        return (BookwhenProblem.TRANSPORT, f"{where}: {result.outcome.value}{detail}")

    status = result.status_code
    if status is not None and not 200 <= status < 300:
        return (BookwhenProblem.HTTP_ERROR, f"{where}: HTTP {status}")
    if not result.has_body:
        return (BookwhenProblem.EMPTY_BODY, f"{where}: HTTP {status} with an empty body")
    return None


def _decode(
    documents: Sequence[str],
    *,
    where: str,
    space_id: str = "",
    label: str = "",
    source_url: str = "",
    horizon_days: int,
    window_start: dt.date,
    window_end: dt.date,
    now: dt.datetime,
    zone_name: str = DEFAULT_ZONE,
) -> BookwhenParse:
    """Read the agenda page (plus any extra pages). Never raises.

    ``documents[0]`` is the registered page; anything after it came out of
    ``calendar_items`` and is a fragment, not a document. Every judgement happens
    here, in order: is this Bookwhen at all, are there rows, does the page say it
    is empty, and did any row carry a usable ``data-event``.
    """
    page = documents[0] if documents else ""
    page_bytes = len(page.encode("utf-8"))
    digest = hashlib.sha1(page.encode("utf-8")).hexdigest()[:16]
    hooks = data_hooks(page)
    calendar = account_id(page)

    def fail(problem: BookwhenProblem, error: str, **extra: Any) -> BookwhenParse:
        return _failure(
            problem,
            error,
            space_id=space_id,
            label=label,
            source_url=source_url,
            hooks_seen=hooks,
            horizon_days=horizon_days,
            window_start=window_start,
            window_end=window_end,
            now=now,
            page_bytes=page_bytes,
            page_digest=digest,
            calendar_id=calendar,
            **extra,
        )

    rows: list[tuple[int, AgendaRow]] = []
    for index, document in enumerate(documents):
        rows.extend(
            (index, row) for row in agenda_rows(document, base_url=source_url)
        )

    if not rows and not looks_like_bookwhen(page):
        # **The unrelated-page check.** A 200 with somebody else's HTML in it —
        # an error page, a login wall, or events.sequoiafabrica.org's catch-all
        # redirect landing somewhere new — must never be read as a calendar with
        # nothing in it. HTTP 200 is not success.
        return fail(
            BookwhenProblem.NOT_BOOKWHEN,
            f"{where}: the body is HTML but is not a Bookwhen agenda page — no "
            f"data-hook starting {AGENDA_HOOK_PREFIX!r} and no data-options "
            f"'calendar' block anywhere in {page_bytes} bytes. data-hooks present: "
            f"{', '.join(hooks) or '<none>'}. Something else answered this URL "
            "(an error page, a login wall, or a redirect), and an adapter that "
            "returned [] here would look exactly like a space with no classes",
        )

    empties = empty_markers(page)

    if not rows:
        if empties:
            # **The legitimate zero.** The agenda rendered and said it has
            # nothing on. ok, no events, reported_empty — issue 0016's
            # allow_zero decides what to do about it.
            return _build(
                _RowTally(),
                hooks_seen=hooks,
                empty_hooks_seen=empties,
                calendar_id=calendar,
                page_digest=digest,
                page_bytes=page_bytes,
                page_count=len(documents),
                reported_empty=True,
                space_id=space_id,
                label=label,
                source_url=source_url,
                horizon_days=horizon_days,
                window_start=window_start,
                window_end=window_end,
                now=now,
                error=(
                    f"{where}: the agenda rendered its empty state "
                    f"({', '.join(empties)}) and carries no rows. This is an "
                    "observation, not a parse failure — the page was found, "
                    "recognized and read; the account simply has nothing booked"
                ),
            )
        return fail(
            BookwhenProblem.NO_ROWS,
            f"{where}: this is a Bookwhen page and it carries no "
            f'<tr data-hook="{AGENDA_ROW_HOOK}"> rows and no empty-state marker '
            f"({'/'.join(EMPTY_HOOKS)}). data-hooks present: "
            f"{', '.join(hooks) or '<none>'}. Either the markup drifted or the "
            "agenda moved behind JavaScript; both want a human, and neither is "
            "an empty calendar",
        )

    tally = _RowTally()
    _build_events(rows, zone=_zone(zone_name), zone_name=zone_name, tally=tally)

    if not tally.events:
        return fail(
            BookwhenProblem.NO_DATES,
            f"{where}: {tally.rows} agenda row(s) and not one usable "
            f"data-event. Expected ev-{{entryid}}-{{YYYYMMDDHHMMSS}}; saw "
            f"{', '.join(tally.bad_values[:5]) or '<no attribute at all>'}. The "
            "attribute was renamed or reformatted — a schema change, not a quiet "
            "week",
            row_count=tally.rows,
            bad_row_count=tally.bad,
            bad_row_values=tally.bad_values,
        )

    notes: list[str] = []
    if tally.bad:
        notes.append(
            f"{tally.bad} of {tally.rows} row(s) carried no usable data-event and "
            f"were skipped ({', '.join(tally.bad_values[:5])})"
        )
    if tally.duplicates:
        notes.append(
            f"{tally.duplicates} row(s) repeated a data-event already seen and were "
            "collapsed — a paginated window overlapping the default page"
        )
    if tally.untitled:
        notes.append(
            f"{tally.untitled} row(s) had no <button> text; their UID is still the "
            "entry id and start, but normalize.py may drop them for having no title"
        )

    return _build(
        tally,
        hooks_seen=hooks,
        empty_hooks_seen=empties,
        calendar_id=calendar,
        page_digest=digest,
        page_bytes=page_bytes,
        page_count=len(documents),
        reported_empty=False,
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


def parse_bookwhen_html(
    result: FetchResult,
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    today: dt.date | None = None,
    now: dt.datetime | None = None,
    strict_content_type: bool = True,
    zone: str = DEFAULT_ZONE,
    extra_documents: Sequence[str] = (),
) -> BookwhenParse:
    """Parse a fetched Bookwhen agenda page. Never raises.

    ``result`` is the registered page, already fetched by the caller. Nothing
    here is configurable from ``sources.yaml``: the row hook and the
    ``data-event`` format are what make this adapter Bookwhen's, so it needs
    neither the ``Fetcher`` nor the ``SourceRef`` — the same shape as
    ``nextdata``, and the reason it is not registered with ``needs_source``.

    ``extra_documents`` is how :func:`fetch_bookwhen_html` hands in pages it
    fetched from ``calendar_items``. It is empty on the nightly path.
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
                    BookwhenProblem.WRONG_CONTENT_TYPE,
                    f"{where}: expected {' or '.join(HTML_CONTENT_TYPES)}, got "
                    f"{result.content_type} — HTTP 200 is not success, and a body "
                    "that is not the page we registered must not be read as an "
                    "agenda with nothing on it",
                    space_id=result.space_id,
                    label=result.label,
                    source_url=result.url,
                    horizon_days=horizon_days,
                    window_start=window_start,
                    window_end=window_end,
                    now=now,
                )

    return _decode(
        [text, *extra_documents],
        where=where,
        space_id=result.space_id,
        label=result.label,
        source_url=result.final_url or result.url,
        horizon_days=horizon_days,
        window_start=window_start,
        window_end=window_end,
        now=now,
        zone_name=zone,
    )


def parse_bookwhen_html_text(
    document: str,
    *,
    space_id: str = "",
    label: str = "",
    source_url: str = "",
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    today: dt.date | None = None,
    now: dt.datetime | None = None,
    zone: str = DEFAULT_ZONE,
    extra_documents: Sequence[str] = (),
) -> BookwhenParse:
    """Parse **one HTML document**. The half with no HTTP in it.

    Split out for the same reason ``ics`` splits ``parse_ics_text``: the tests,
    and a human diffing two files out of ``raw/``, must be able to reach the
    parser without manufacturing a :class:`~pipeline.fetch.FetchResult`.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    today = today or now.date()
    return _decode(
        [document, *extra_documents],
        where=f"{space_id}:{label}" if space_id or label else "<bookwhen_html>",
        space_id=space_id,
        label=label,
        source_url=source_url,
        horizon_days=horizon_days,
        window_start=today,
        window_end=today + dt.timedelta(days=horizon_days),
        now=now,
        zone_name=zone,
    )


# --------------------------------------------------------------------------- fetching


def fetch_bookwhen_html(
    fetcher: Fetcher,
    ref: SourceRef,
    *,
    horizon_days: int | None = None,
    today: dt.date | None = None,
    now: dt.datetime | None = None,
    conditional: bool = False,
    strict_content_type: bool = True,
    paginate: bool = False,
    page_limit: int = 50,
) -> BookwhenParse:
    """Fetch a registered agenda page and parse it. All in one call.

    The counterpart to :func:`pipeline.adapters.nextdata.fetch_nextdata`, and the
    entry point for the repair workflow: one source, one page, no run.

    **One request by default.** ``paginate=True`` adds *at most one* more, to
    ``/{account}/calendar_items``, and only when the first page actually looks
    short — :attr:`BookwhenParse.looks_paginated`, i.e. a full 20 rows whose last
    event still lands inside the horizon. Sequoia Fabrica never trips it. That
    endpoint answers with executable JavaScript rather than JSON, which is a
    thing to touch deliberately and with a reason, never by default; the extra
    request still goes through the same rate limiter, the same ``robots.txt``
    decision and the same ``raw/`` archive as the first.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    today = today or now.date()
    if horizon_days is None:
        horizon_days = fetcher.registry.horizon_days

    _space, source = ref
    result = fetcher.fetch_url(ref, request_url(source), conditional=conditional)
    parsed = parse_bookwhen_html(
        result,
        horizon_days=horizon_days,
        today=today,
        now=now,
        strict_content_type=strict_content_type,
    )
    if not paginate or not parsed.looks_paginated:
        return parsed

    more = calendar_items_url(
        result.final_url or result.url,
        offset=parsed.row_count,
        limit=page_limit,
        calendar=parsed.calendar_id,
    )
    if more is None:  # pragma: no cover - defensive; the slug is in the registry
        return parsed

    extra = fetcher.fetch_url(ref, more, conditional=False)
    if extra.outcome is not Outcome.FETCHED or not extra.has_body:
        # The default page is still a real calendar. A failed second request is
        # a note, not a reason to lose the twenty events we already have.
        return parsed

    return parse_bookwhen_html(
        result,
        horizon_days=horizon_days,
        today=today,
        now=now,
        strict_content_type=strict_content_type,
        extra_documents=[html_from_calendar_items(extra.text)],
    )


#: The adapter entry point named by ``sources.yaml``'s ``adapter: bookwhen_html``.
parse = parse_bookwhen_html


__all__ = [
    "AGENDA_HOOK_PREFIX",
    "AGENDA_ROW_HOOK",
    "CALENDAR_ITEMS_PATH",
    "DATA_EVENT_RE",
    "DEFAULT_PAGE_SIZE",
    "DEFAULT_ZONE",
    "EMPTY_HOOKS",
    "STAMP_FORMAT",
    "AgendaRow",
    "BookwhenEvent",
    "BookwhenParse",
    "BookwhenProblem",
    "account_id",
    "account_options",
    "account_slug",
    "agenda_rows",
    "calendar_items_url",
    "data_hooks",
    "empty_markers",
    "fetch_bookwhen_html",
    "html_from_calendar_items",
    "looks_like_bookwhen",
    "parse",
    "parse_bookwhen_html",
    "parse_bookwhen_html_text",
    "parse_data_event",
]
