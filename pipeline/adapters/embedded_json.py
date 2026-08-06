"""Embedded-JSON adapter: a **named** ``<script type="application/json" id="…">``.

A server-rendered page that ships its whole catalog inside one JSON blob and
filters it in the browser. The adapter reads that blob by ``id`` and maps its
bespoke shape through a :class:`FieldMap`.

Why this is a new adapter and not one of the four that already exist
--------------------------------------------------------------------

- ``jsonld`` is **schema.org-specific**: it looks for ``application/ld+json``
  and for ``@type: Event``. The Crucible's product pages carry
  ``schema.org/Course`` with no ``startDate``, no ``offers`` and no
  ``hasCourseInstance``, so a ``jsonld`` source there returns empty forever
  without ever erroring. That is recorded in :mod:`pipeline.adapters.jsonld`
  and it is the reason this module exists.
- ``nextdata`` (issue 0023) is hardcoded to ``__NEXT_DATA__`` and to
  Eventbrite's ``props.pageProps.upcomingEvents`` shape.
- ``json`` (issue 0022) expects a standalone JSON *document* at a URL.

Practically this is ``nextdata`` generalized: a configurable ``script_id`` plus
a field map with a dotted :attr:`FieldMap.items` path. ``nextdata`` could be
reimplemented on top of it as ``script_id="__NEXT_DATA__"`` with
``items="props.pageProps.upcomingEvents"``, which is why the path resolver here
walks nested objects and list indices rather than only top-level keys.

Registered consumer (2026-08-05 survey)
---------------------------------------

===============================================  =============================
``the-crucible`` / ``course-catalog-blob``        ``/course-search/`` →
                                                  ``<script id="ac-course-data"
                                                  type="application/json">``.
                                                  **132 KB, 216 courses, 478
                                                  offerings, 271 inside 120
                                                  days.** ``trust: 100``.
===============================================  =============================

The five things this source teaches, all verified rather than assumed
---------------------------------------------------------------------

**1. ``start_dates[]`` are Unix epoch SECONDS, local America/Los_Angeles.**
``1786064400`` decodes to ``08/06/26 6:00 PM`` and ``1790902800`` to
``10/01/26 6:00 PM``, matching the product page's ``Class.Date`` attribute to
the minute. An epoch is an instant, so the offset is never a guess — but
:attr:`~pipeline.adapters.ics.IcsEvent.tz` still has to carry a **resolvable
IANA name**, because :func:`pipeline.normalize.day_start_utc` and issue 0016's
health filter both call ``ZoneInfo`` on it and that filter runs outside the
per-source ``try``/``except``. So every timed event here carries
:data:`DEFAULT_ZONE`, matching ``defaults.timezone`` in ``sources.yaml``.

**2. Each ``start_date`` is one RUN of a course, not one meeting.** A 5-week,
20-hour ``Glass Fusing and Slumping Lab`` running Tuesdays and Thursdays has
**one** ``start_date`` — roughly ten meetings collapsed into a single entry,
which is exactly what a merged calendar wants. ``Woodworking I`` has five
``start_dates`` because the course runs five separate times in the year.

**3. ``hours`` is total course hours and must never become ``DTEND``.**
``Woodworking I`` is ``hours: 40.0``; ``DTSTART + 40h`` renders a two-day block
in every calendar client. The field is carried on
:attr:`EmbeddedJsonEvent.hours` for diagnostics and :attr:`EmbeddedJsonEvent.end`
is left ``None``. Deriving an end for single-meeting courses (``format:
Weekend``/``Weekday`` with ``hours <= 8``) is tempting and is deliberately not
done: the blob has no per-meeting data at all, so the "single meeting" test is
an inference about a field that was never measured. Ask the registrar instead —
``spaces/the-crucible.md`` records that lead.

**4. Nine courses carry an empty ``start_dates`` array.** They are skipped and
**counted** (:attr:`EmbeddedJsonParse.undated_item_count`). A dateless event is
not publishable and a silently smaller catalog is the failure mode this project
keeps designing against.

**5. The blob stores HTML entities.** It is ``Glass Fusing &amp; Slumping``,
never ``Glass Fusing & Slumping`` — script bodies are raw text in HTML, so
nothing decodes them on the way out of the parser. Every text field goes through
:func:`html.unescape` here, so the emitted title is the decoded form and issue
0010's filters (which are entity-insensitive in both directions) match either
way.

Where someone will try to be clever, and why it does not work
--------------------------------------------------------------

**There is no XHR endpoint and no server-side filtering.** ``?department=
Blacksmithing``, ``?department=ZZZ-NOT-A-DEPT`` and ``?ac_department=
blacksmithing`` all return the *identical* 216-course blob; the site's search UI
filters this blob in client-side JavaScript. Adding a query parameter to narrow
the fetch would therefore download the same 132 KB and prove nothing. See the
comment in :func:`fetch_embedded_json`.

**``/shop/`` carries a byte-identical blob — do not ingest both.** Confirmed by
Python object equality on 2026-08-05. :attr:`EmbeddedJsonParse.blob_digest` is
published so a human can re-confirm that in one line without registering a
second source that would double every event and then be collapsed by dedupe.

**``stocks[]`` is not used.** It is the same length as ``start_dates`` on the
sampled course, which *suggests* index alignment, and that is not the same as
knowing it. Publishing a seat count against the wrong date would be wrong data
that looks like good data. ``spaces/the-crucible.md`` records how to verify it.

**No location, on purpose.** The catalog carries no location field of any kind —
not in the blob, not in the Store API, not in the product HTML. ``location`` is
left ``None`` so :class:`pipeline.normalize.VenuePolicy` applies the space's
mandatory ``address_override``, exactly as it does for The Box Shop's empty
JSON-LD ``location``.

Implemented by issue 0021.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html as html_module
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import lxml.etree
from extruct.utils import parse_html

from pipeline.adapters.ics import DEFAULT_HORIZON_DAYS, IcsEvent, IcsParse
from pipeline.config import SourceRef
from pipeline.fetch import (
    HTML_CONTENT_TYPES,
    FetchResult,
    Fetcher,
    Outcome,
    request_url,
)

# --------------------------------------------------------------------------- knobs

#: The zone the epochs are local to, and the IANA name every timed event carries
#: in :attr:`~pipeline.adapters.ics.IcsEvent.tz`. Matches ``defaults.timezone``
#: in ``sources.yaml``. Downstream calls ``ZoneInfo`` on this — an offset string
#: would raise there, outside the per-source try/except.
DEFAULT_ZONE = "America/Los_Angeles"

#: Epochs outside this range are rejected as sentinels rather than published.
#: ``0`` is the one that actually occurs: the WooCommerce side of this same
#: catalog carries two literal ``01/01/70 12:00 am`` class-date terms, which a
#: naive ``%y`` parse maps to **2070** and which then sail past every horizon
#: check without erroring. An epoch of 0 lands in 1970 instead, which is merely
#: out of horizon — so it is dropped *explicitly* and counted, not left to be
#: filtered by accident.
MIN_EPOCH = 631152000  # 1990-01-01T00:00:00Z
MAX_EPOCH = 4102444800  # 2100-01-01T00:00:00Z

#: How far into a body to look before deciding what kind of document it is.
_SNIFF_BYTES = 4096

#: Every ``<script>`` on the page. The ``type`` is filtered in Python rather
#: than in the XPath so ``application/json; charset=utf-8`` still matches and so
#: a blob carrying the right ``id`` with the *wrong* type can be reported as
#: what it is instead of vanishing.
_SCRIPT_XPATH = lxml.etree.XPath("descendant-or-self::script")

#: Content types tolerated when the named blob is nonetheless present. Some
#: hosts serve pages as ``text/plain``; that is sloppy, not dishonest.
LENIENT_CONTENT_TYPES: tuple[str, ...] = ("text/plain", "application/octet-stream")


# --------------------------------------------------------------------------- outcomes


class EmbeddedJsonProblem(str, Enum):
    """Why an :class:`EmbeddedJsonParse` is not usable. ``NONE`` means it is.

    The first five mirror :class:`~pipeline.adapters.ics.IcsProblem` so the
    CLI's ``record.problem`` vocabulary stays one vocabulary. The rest are the
    failures only a named-blob adapter can have, and they are kept apart because
    "0 events" from each of them means something different to a human reading
    ``health.json`` at 09:00.
    """

    NONE = "none"
    TRANSPORT = "transport"
    NOT_MODIFIED = "not_modified"
    EMPTY_BODY = "empty_body"
    WRONG_CONTENT_TYPE = "wrong_content_type"

    #: Non-2xx on the registered URL.
    HTTP_ERROR = "http_error"
    #: **No ``script_id`` was configured.** A configuration error, not a source
    #: failure: without a name there is nothing to look for, and reading the
    #: first ``application/json`` blob on the page would be a guess.
    NO_SCRIPT_ID = "no_script_id"
    #: The page parsed and carries no ``<script type="application/json">`` with
    #: that ``id``. A drifted source: the catalog moved, was renamed, or went
    #: behind an XHR. Never an empty calendar.
    SCRIPT_NOT_FOUND = "script_not_found"
    #: The blob was found and is not valid JSON.
    NOT_JSON = "not_json"
    #: Valid JSON, but no list of item objects could be located in it — the
    #: shape changed under the same ``id``.
    NO_ITEMS = "no_items"
    #: Items were found and **not one** of them carried a usable date. 216
    #: courses with 0 dates is a schema change, not a quiet holiday.
    NO_DATES = "no_dates"
    #: The body could not be parsed as a document at all.
    UNPARSEABLE = "unparseable"


# --------------------------------------------------------------------------- field map


@dataclass(frozen=True)
class FieldMap:
    """Which keys of a bespoke item object mean what.

    Defaults describe The Crucible's ``ac-course-data`` shape. Every value is a
    **dotted path** resolved by :func:`resolve_path`, so ``"venue.name"`` and
    ``"offers.0.price"`` work as well as a bare key — that is what makes this
    adapter general enough for ``nextdata`` to be rebuilt on top of it.
    """

    #: Dotted path to the list of item objects. ``None`` means "the document
    #: itself if it is a list, otherwise the first list of objects in it" —
    #: which is what ``ac-course-data`` needs, since the blob *is* the array.
    items: str | None = None

    #: The stable per-item identity. The Crucible's ``id`` is the WordPress post
    #: ID, so ``{id}:{start_epoch}`` is stable across runs by construction.
    uid: str = "id"
    title: str = "title"
    url: str = "url"

    #: The list of start instants. Each entry is **one run of the course**.
    starts: str = "start_dates"
    #: ``"epoch_seconds"`` | ``"epoch_millis"`` | ``"iso"``.
    start_format: str = "epoch_seconds"

    description: str | None = None
    #: Source categories. Issue 0010's ``categories_exclude`` runs on these, and
    #: for this space that is deliberately ``department`` — the WooCommerce
    #: ``categories`` are polluted with 60+ one-off per-product categories equal
    #: to the class title.
    categories: str = "department"
    price: str = "price"
    member_price: str | None = "member_price"
    image: str | None = "image_url"
    in_stock: str | None = "is_in_stock"

    #: Descriptive fields kept as provenance. **``hours`` is never used for an
    #: end time** — see the module docstring.
    hours: str | None = "hours"
    audience: str | None = "audience"
    level: str | None = "level"
    item_format: str | None = "format"
    time_of_day: str | None = "time_of_day"
    days_text: str | None = "day"

    #: Location, if a source ever has one. The Crucible does not, and leaving it
    #: ``None`` is what hands the decision to ``VenuePolicy``.
    location: str | None = None


#: The Crucible's shape, and the default. Named so a registry comment and a
#: traceback can refer to the same thing.
CRUCIBLE_FIELD_MAP = FieldMap()


# --------------------------------------------------------------------------- events


@dataclass(frozen=True)
class EmbeddedJsonEvent(IcsEvent):
    """One course *run*, in the intermediate shape the ``ics`` adapter produces.

    A subclass rather than a parallel class, for the same reason
    :class:`~pipeline.adapters.tribe_rest.TribeEvent` is one: ``normalize.py``
    consumes every adapter through :func:`~pipeline.normalize.from_ics_event`,
    and the moment a second shape appears one of them starts quietly losing
    fields.
    """

    #: The source's own item id, as text. ``uid`` is ``{item_id}:{start_epoch}``.
    item_id: str | None = None
    #: The epoch second this run starts at, exactly as the blob wrote it. Half
    #: of the UID, and the reason it is stable across runs.
    start_epoch: int | None = None

    #: ``offers.price`` equivalent, as **source text**, never a float. "sliding
    #: scale $10-30" and "free for members" are real values in this dataset.
    cost: str | None = None
    member_cost: str | None = None

    #: **Total course hours, not one meeting's duration.** Carried for
    #: diagnostics; :attr:`end` is ``None`` because ``DTSTART + hours`` renders
    #: a 40-hour block. Do not be tempted.
    hours: float | None = None

    audience: str | None = None
    level: str | None = None
    course_format: str | None = None
    time_of_day: str | None = None
    #: The meeting days as written. ``"Tuesday; Thursday"`` — and one live value
    #: is ``"Monday, Tuesday, Wednesday, Thursday "``, commas and a trailing
    #: space, so a naive ``split("; ")`` mis-parses it. Kept verbatim.
    days_text: str | None = None

    #: ``is_in_stock``. A sold-out class is still a real public event and is
    #: kept by default; the registry can exclude it if a space asks.
    in_stock: bool = True
    image: str | None = None

    #: The ``id`` of the blob this came out of. Provenance for a page that
    #: carries several ``application/json`` scripts.
    script_id: str = ""

    @property
    def price(self) -> str | None:
        """Alias :attr:`cost` under the canonical schema's name.

        :func:`pipeline.normalize.from_ics_event` reads ``price`` off whatever
        the adapter handed it.
        """
        return self.cost


# --------------------------------------------------------------------------- blocks


@dataclass(frozen=True)
class JsonScript:
    """One ``<script>`` on the page that might be the blob we were sent for."""

    id: str
    type: str
    text: str

    @property
    def is_json(self) -> bool:
        return self.type.split(";", 1)[0].strip().lower() == "application/json"

    @property
    def byte_count(self) -> int:
        return len(self.text.encode("utf-8"))


def json_scripts(document: str) -> tuple[JsonScript, ...]:
    """Every ``<script>`` element on the page, in document order.

    Both JSON and non-JSON: a page whose ``ac-course-data`` has become
    ``type="text/javascript"`` should be *reported as that*, not reported as
    missing. Uses ``extruct``'s HTML parser so this module and ``jsonld`` see
    the same document.
    """
    tree = parse_html(document, "UTF-8")
    return tuple(
        JsonScript(
            id=(node.get("id") or "").strip(),
            type=(node.get("type") or "").strip(),
            # Script bodies are raw text in HTML, so an ``&amp;`` written into
            # the JSON arrives here verbatim. That is deliberate and it is why
            # every text field goes through html.unescape below.
            text=str(node.xpath("string()")),
        )
        for node in _SCRIPT_XPATH(tree)
    )


def json_script_ids(scripts: Iterable[JsonScript]) -> tuple[str, ...]:
    """The ``id`` of every ``application/json`` blob, for diagnostics.

    This is what turns "the blob is missing" into "the blob is missing, and the
    page carries ``wp-block-settings`` and ``fusion-slider-data`` instead".
    """
    return tuple(script.id for script in scripts if script.is_json and script.id)


def find_script(document: str, script_id: str) -> tuple[JsonScript | None, tuple[JsonScript, ...]]:
    """``(the named application/json blob, every script on the page)``.

    Matching is on ``id`` **and** type. A same-``id`` element with another type
    is left for the caller to report, which is the difference between "the
    catalog moved" and "the catalog is now rendered by JavaScript".
    """
    scripts = json_scripts(document)
    for script in scripts:
        if script.id == script_id and script.is_json:
            return script, scripts
    return None, scripts


# --------------------------------------------------------------------------- paths


def resolve_path(payload: Any, path: str | None) -> Any:
    """Walk a dotted path: ``"props.pageProps.upcomingEvents"``, ``"terms.0"``.

    Returns ``None`` for any step that does not exist rather than raising —
    a missing path is a reportable shape change, and this function is called
    once per field per item.
    """
    if path is None:
        return None
    node = payload
    for part in path.split("."):
        if node is None:
            return None
        if isinstance(node, dict):
            node = node.get(part)
            continue
        if isinstance(node, (list, tuple)):
            try:
                node = node[int(part)]
            except (ValueError, IndexError):
                return None
            continue
        return None
    return node


def _is_item_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(v, dict) for v in value)


def find_items(payload: Any, path: str | None = None) -> list[dict[str, Any]]:
    """The list of item objects inside a decoded blob.

    With ``path`` set, only that path is consulted — an explicit map must not be
    silently "helped" by a search that finds a different list. With no path, the
    document itself is used when it is already a list (``ac-course-data`` is a
    bare array of 216 courses), and otherwise the first top-level value that is
    a non-empty list of objects is taken.
    """
    if path:
        found = resolve_path(payload, path)
        return list(found) if _is_item_list(found) else []
    if _is_item_list(payload):
        return list(payload)
    if isinstance(payload, dict):
        for value in payload.values():
            if _is_item_list(value):
                return list(value)
    return []


# --------------------------------------------------------------------------- text


def _clean(value: Any) -> str | None:
    """Trim and HTML-unescape one text field, or ``None`` if it is empty.

    **The reason this module exists in the entity sense.** The blob stores
    ``Glass Fusing &amp; Slumping`` and ``Woodworking I &#8211; 5 weeks``,
    because a ``<script>`` body is raw text in HTML and nothing decoded it on
    the way out. A calendar full of ``&amp;`` is the kind of thing nobody files
    a bug for.
    """
    if value is None or isinstance(value, (dict, list, bool)):
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


def _first(value: Any) -> str | None:
    strings = _strings(value)
    return strings[0] if strings else None


def _float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "instock")
    return default


def _money(value: Any) -> str | None:
    """A price as **source text**, with ``$`` added only to a bare number.

    The same rule ``tribe_rest`` and ``jsonld`` follow, for the same reason:
    "sliding scale $10-30" turns into a wrong number and "free for members"
    turns into ``None`` the moment anyone calls ``float()`` on it.
    """
    text = _clean(value)
    if text is None:
        return None
    if text.startswith("$"):
        return text
    try:
        amount = float(text)
    except ValueError:
        return text
    return f"${amount:,.2f}".replace(".00", "") if amount == int(amount) else f"${amount:,.2f}"


# --------------------------------------------------------------------------- dates


def _zone(name: str = DEFAULT_ZONE) -> ZoneInfo | None:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):  # pragma: no cover
        return None


def parse_epoch(
    value: Any, *, zone: ZoneInfo | None = None, millis: bool = False
) -> tuple[dt.datetime | None, int | None]:
    """``1786064400`` → ``2026-08-06 18:00:00-07:00``, plus the epoch itself.

    Returns ``(None, None)`` for anything unusable, including the ``0``
    sentinel — see :data:`MIN_EPOCH`. Strings are accepted because a blob that
    quotes its numbers is a JSON-encoder detail, not a different source.

    An epoch is an **instant**, so no offset is being guessed here: converting
    it in ``America/Los_Angeles`` recovers exactly the wall clock the product
    page prints, on either side of a DST boundary. ``1794362400`` is
    2026-11-10 18:00 **PST** (UTC-8) while ``1786064400`` is 2026-08-06 18:00
    **PDT** (UTC-7), and both say "6:00 PM" to a student.
    """
    if isinstance(value, bool):
        return None, None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None, None
        try:
            value = float(text)
        except ValueError:
            return None, None
    if not isinstance(value, (int, float)):
        return None, None

    epoch = int(value // 1000) if millis else int(value)
    if not MIN_EPOCH <= epoch <= MAX_EPOCH:
        return None, None
    target = zone or _zone()
    if target is None:  # pragma: no cover - tzdata missing
        return dt.datetime.fromtimestamp(epoch, dt.timezone.utc), epoch
    return dt.datetime.fromtimestamp(epoch, target), epoch


def parse_iso(value: Any, *, zone: ZoneInfo | None = None) -> tuple[dt.datetime | None, int | None]:
    """``"2026-08-06T18:00:00-07:00"`` → an aware datetime and its epoch.

    Offered for the ``start_format: "iso"`` field map. A value with no offset is
    read as local to ``zone``, because a source that writes wall-clock strings
    means the venue's wall clock; nothing naive ever leaves this module.
    """
    text = _clean(value)
    if not text:
        return None, None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None, None
    if parsed.tzinfo is None:
        target = zone or _zone()
        if target is None:  # pragma: no cover - tzdata missing
            return None, None
        parsed = parsed.replace(tzinfo=target)
    return parsed, int(parsed.timestamp())


# --------------------------------------------------------------------------- result


@dataclass(frozen=True)
class EmbeddedJsonParse(IcsParse):
    """The adapter's return value. Same surface as :class:`IcsParse`, plus the blob.

    Subclasses :class:`~pipeline.adapters.ics.IcsParse` so every consumer
    written against the ICS adapter — ``normalize_ics``, the CLI's
    ``SourceRecord``, issue 0016's gates — works unchanged.
    """

    problem: EmbeddedJsonProblem = EmbeddedJsonProblem.NONE  # type: ignore[assignment]

    #: The ``id`` we were sent for.
    script_id: str = ""
    #: Every ``application/json`` blob id on the page, in document order. What
    #: makes a missing blob diagnosable in one line.
    script_ids_seen: tuple[str, ...] = ()
    #: Size of the blob as fetched. 132 KB for The Crucible.
    blob_bytes: int = 0
    #: ``sha1(blob)[:16]``. Published so ``/course-search/`` and ``/shop/`` can
    #: be confirmed byte-identical without registering the second one.
    blob_digest: str = ""

    #: Item objects in the blob. 216 courses.
    item_count: int = 0
    #: Items skipped because their ``start_dates`` array was empty. Nine, and
    #: they are counted rather than quietly absent.
    undated_item_count: int = 0
    #: Item ids that were skipped, for the log. Capped.
    undated_ids: tuple[str, ...] = ()
    #: Start values that were present and unusable — the epoch sentinel, a
    #: string that is not a number. Counted separately from "no dates at all".
    bad_date_count: int = 0
    #: Items with no usable id. Their UID falls back to normalize's sha1.
    missing_id_count: int = 0

    @property
    def ok(self) -> bool:
        """True when the named blob was found, decoded, and held dated items.

        Zero events with ``ok=True`` is possible (everything past the horizon)
        and is ``allow_zero``'s problem — but this space should never be near
        zero, and ``sources.yaml`` deliberately does **not** set ``allow_zero``
        for it.
        """
        return self.problem is EmbeddedJsonProblem.NONE

    @property
    def offering_count(self) -> int:
        """Raw ``start_dates`` entries seen across every item: 478.

        The same number as :attr:`~pipeline.adapters.ics.IcsParse.vevent_count`,
        under the name the research file uses.
        """
        return self.vevent_count

    @property
    def dated_item_count(self) -> int:
        return self.item_count - self.undated_item_count

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        head = f"{self.space_id}:{self.label}" if self.space_id or self.label else "<embedded_json>"
        if not self.ok:
            return f"{head} FAILED {self.problem.value}: {self.error}"
        return (
            f"{head} {self.event_count} events in {self.horizon_days}d "
            f"(from {self.item_count} items / {self.offering_count} offerings in "
            f"#{self.script_id}, {self.blob_bytes}B)"
        )


# --------------------------------------------------------------------------- building


def _sort_key(event: EmbeddedJsonEvent) -> tuple[dt.date, int, dt.time, str]:
    """Same ordering rule as the ICS adapter: day, all-day first, then time."""
    start = event.start
    if isinstance(start, dt.datetime):
        moment = start.astimezone(dt.timezone.utc) if start.tzinfo else start
        return (moment.date(), 1, moment.time(), event.uid or "")
    return (start, 0, dt.time.min, event.uid or "")


@dataclass
class _ItemTally:
    """Internal: what the item walk saw, so the counts are not recomputed."""

    offerings: int = 0
    undated: int = 0
    undated_ids: list[str] = field(default_factory=list)
    bad_dates: int = 0
    missing_ids: int = 0
    events: list[EmbeddedJsonEvent] = field(default_factory=list)


def _build_events(
    item: dict[str, Any],
    *,
    field_map: FieldMap,
    script_id: str,
    zone: ZoneInfo | None,
    tally: _ItemTally,
) -> None:
    """One item → **one event per ``start_date``**, appended to ``tally``.

    Not one per meeting: a 5-week course with ten sessions and one
    ``start_date`` is one event, which is what a merged calendar wants.
    """
    raw_starts = resolve_path(item, field_map.starts)
    starts: Sequence[Any]
    if raw_starts is None:
        starts = ()
    elif isinstance(raw_starts, (list, tuple)):
        starts = raw_starts
    else:
        starts = (raw_starts,)

    item_id = _clean(resolve_path(item, field_map.uid))
    if item_id is None:
        tally.missing_ids += 1

    if not starts:
        # The nine dateless courses. Counted, never emitted: normalize would
        # drop them as NO_START anyway and a silently smaller catalog is the
        # failure this project keeps designing against.
        tally.undated += 1
        if item_id:
            tally.undated_ids.append(item_id)
        return

    title = _clean(resolve_path(item, field_map.title))
    url = _clean(resolve_path(item, field_map.url))
    categories = _strings(resolve_path(item, field_map.categories))
    cost = _money(resolve_path(item, field_map.price))
    member_cost = _money(resolve_path(item, field_map.member_price))
    hours = _float(resolve_path(item, field_map.hours))
    in_stock = _bool(resolve_path(item, field_map.in_stock))

    # "$45 / $30 members" is the shape normalize.py documents for price, and it
    # is source text throughout — nothing here is ever parsed to a float.
    price_text = cost
    if cost and member_cost and member_cost != cost:
        price_text = f"{cost} / {member_cost} members"

    for raw_start in starts:
        tally.offerings += 1
        if field_map.start_format == "iso":
            start, epoch = parse_iso(raw_start, zone=zone)
        else:
            start, epoch = parse_epoch(
                raw_start, zone=zone, millis=field_map.start_format == "epoch_millis"
            )
        if start is None or epoch is None:
            # The epoch-0 sentinel and anything else unusable. Explicit, and
            # counted: it is not the same thing as a course with no dates.
            tally.bad_dates += 1
            continue

        tally.events.append(
            EmbeddedJsonEvent(
                # {id}:{start_epoch}. The id is the WordPress post ID and the
                # epoch is what the blob wrote, so this is stable across runs by
                # construction. normalize.py namespaces it to
                # {space_id}:{id}:{start_epoch}.
                uid=f"{item_id}:{epoch}" if item_id else None,
                title=title,
                start=start,
                # **Never DTSTART + hours.** `hours` is the total for the whole
                # course; Woodworking I is 40.0 and would render a two-day block.
                end=None,
                all_day=False,
                multi_day=False,
                days=1,
                # A resolvable IANA name, not an offset: normalize.day_start_utc
                # and issue 0016's health filter both call ZoneInfo on this, and
                # that filter runs outside the per-source try/except.
                tz=DEFAULT_ZONE if zone is not None else None,
                source_tz=None,
                dtstart_form="epoch",
                # No location field exists anywhere in this catalog. Leaving it
                # None hands the decision to VenuePolicy and the space's
                # mandatory address_override.
                location=_clean(resolve_path(item, field_map.location)),
                description=_clean(resolve_path(item, field_map.description)),
                url=url,
                categories=categories,
                status=None,
                organizer=None,
                # Each start_date is an independent run, not an RRULE
                # occurrence, and the UID already carries the epoch.
                recurring=False,
                last_modified=None,
                dtstamp=None,
                item_id=item_id,
                start_epoch=epoch,
                cost=price_text,
                member_cost=member_cost,
                hours=hours,
                audience=_first(resolve_path(item, field_map.audience)),
                level=_first(resolve_path(item, field_map.level)),
                course_format=_first(resolve_path(item, field_map.item_format)),
                time_of_day=_first(resolve_path(item, field_map.time_of_day)),
                days_text=_first(resolve_path(item, field_map.days_text)),
                in_stock=in_stock,
                image=_clean(resolve_path(item, field_map.image)),
                script_id=script_id,
            )
        )


def _build(
    tally: _ItemTally,
    *,
    item_count: int,
    script_id: str,
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
    problem: EmbeddedJsonProblem = EmbeddedJsonProblem.NONE,
    error: str | None = None,
) -> EmbeddedJsonParse:
    """Assemble the walk into the returned :class:`EmbeddedJsonParse`.

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

    return EmbeddedJsonParse(
        events=tuple(sorted(kept, key=_sort_key)),
        problem=problem,
        error=error,
        space_id=space_id,
        label=label,
        source_url=source_url,
        # Raw = offerings, i.e. every start_dates entry: 478 for The Crucible.
        # The health gates count `event_count`, which is 271.
        vevent_count=tally.offerings,
        recurring_vevent_count=0,
        horizon_days=horizon_days,
        window_start=window_start,
        window_end=window_end,
        parsed_at=now,
        script_id=script_id,
        script_ids_seen=tuple(script_ids_seen),
        blob_bytes=blob_bytes,
        blob_digest=blob_digest,
        item_count=item_count,
        undated_item_count=tally.undated,
        undated_ids=tuple(tally.undated_ids[:20]),
        bad_date_count=tally.bad_dates,
        missing_id_count=tally.missing_ids,
    )


def _failure(
    problem: EmbeddedJsonProblem,
    error: str,
    *,
    space_id: str = "",
    label: str = "",
    source_url: str = "",
    script_id: str = "",
    script_ids_seen: Sequence[str] = (),
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    window_start: dt.date | None = None,
    window_end: dt.date | None = None,
    now: dt.datetime | None = None,
    blob_bytes: int = 0,
    item_count: int = 0,
) -> EmbeddedJsonParse:
    """A reported failure. Never raised — see the ``ics`` module docstring."""
    return EmbeddedJsonParse(
        problem=problem,
        error=error,
        space_id=space_id,
        label=label,
        source_url=source_url,
        script_id=script_id,
        script_ids_seen=tuple(script_ids_seen),
        horizon_days=horizon_days,
        window_start=window_start,
        window_end=window_end,
        parsed_at=now,
        blob_bytes=blob_bytes,
        item_count=item_count,
    )


# --------------------------------------------------------------------------- decoding


def _transport_problem(
    result: FetchResult, *, where: str
) -> tuple[EmbeddedJsonProblem, str] | None:
    """Everything that can be wrong before the body is worth looking at."""
    if result.outcome is Outcome.NOT_MODIFIED:
        return (
            EmbeddedJsonProblem.NOT_MODIFIED,
            f"{where}: HTTP 304, unchanged since the last run — reuse the stored "
            "events rather than treating this as zero",
        )
    if result.outcome is not Outcome.FETCHED:
        detail = f" ({result.reason})" if result.reason else ""
        return (EmbeddedJsonProblem.TRANSPORT, f"{where}: {result.outcome.value}{detail}")

    status = result.status_code
    if status is not None and not 200 <= status < 300:
        return (EmbeddedJsonProblem.HTTP_ERROR, f"{where}: HTTP {status}")
    if not result.has_body:
        return (EmbeddedJsonProblem.EMPTY_BODY, f"{where}: HTTP {status} with an empty body")
    return None


def looks_like_html(text: str) -> bool:
    head = text[:_SNIFF_BYTES].lstrip("﻿ \t\r\n").lower()
    return head.startswith(("<!doctype html", "<html", "<head", "<body", "<!--"))


def _decode(
    document: str,
    *,
    script_id: str | None,
    field_map: FieldMap,
    where: str,
    space_id: str = "",
    label: str = "",
    source_url: str = "",
    horizon_days: int,
    window_start: dt.date,
    window_end: dt.date,
    now: dt.datetime,
    zone: ZoneInfo | None,
) -> EmbeddedJsonParse:
    """Read one HTML document for the named blob. Never raises.

    Every judgement CLAUDE.md asks for happens here, in order: is there a name
    to look for, is the blob on the page, is it JSON, is it the shape we mapped,
    and did anything in it carry a date. They are five different problems with
    five different fixes, and "0 events" from each of them means something
    different at 09:00.
    """

    def fail(
        problem: EmbeddedJsonProblem,
        error: str,
        *,
        seen: Sequence[str] = (),
        blob_bytes: int = 0,
        item_count: int = 0,
    ) -> EmbeddedJsonParse:
        return _failure(
            problem,
            error,
            space_id=space_id,
            label=label,
            source_url=source_url,
            script_id=script_id or "",
            script_ids_seen=seen,
            horizon_days=horizon_days,
            window_start=window_start,
            window_end=window_end,
            now=now,
            blob_bytes=blob_bytes,
            item_count=item_count,
        )

    if not script_id:
        # A configuration error, and it is *this* adapter's whole premise: the
        # blob is identified by name. Reading the first application/json script
        # on the page instead would be a guess, and a guess that happened to
        # work once would be worse than one that never did. sources.yaml already
        # refuses an embedded_json source without script_id (pipeline/config.py).
        return fail(
            EmbeddedJsonProblem.NO_SCRIPT_ID,
            f"{where}: no script_id was supplied. The embedded_json adapter reads a "
            "*named* <script type=\"application/json\" id=\"…\"> blob; without a name "
            "there is nothing to look for, and picking the first JSON blob on the "
            "page would be a guess. Set script_id on the source in sources.yaml "
            "(The Crucible uses 'ac-course-data')",
        )

    script, scripts = find_script(document, script_id)
    seen = json_script_ids(scripts)

    if script is None:
        wrong_type = next(
            (item for item in scripts if item.id == script_id and not item.is_json), None
        )
        detail = (
            f" A <script id=\"{script_id}\"> is present but its type is "
            f"{wrong_type.type!r}, not application/json."
            if wrong_type
            else ""
        )
        return fail(
            EmbeddedJsonProblem.SCRIPT_NOT_FOUND,
            f"{where}: no <script type=\"application/json\" id=\"{script_id}\"> on the "
            f"page.{detail} application/json blob ids present: "
            f"{', '.join(seen) or '<none>'}. This is a drifted source, not an empty "
            "catalog — the blob was renamed, moved, or went behind an XHR endpoint, "
            "and an adapter that returned [] here would look exactly like a space "
            "with no classes",
            seen=seen,
        )

    blob = script.text
    blob_bytes = script.byte_count
    digest = hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]

    try:
        payload = json.loads(blob)
    except ValueError as exc:
        head = blob.strip()[:60].replace("\n", " ")
        return fail(
            EmbeddedJsonProblem.NOT_JSON,
            f"{where}: <script id=\"{script_id}\"> is {blob_bytes} bytes and is not "
            f"valid JSON ({exc}); it starts {head!r}",
            seen=seen,
            blob_bytes=blob_bytes,
        )

    items = find_items(payload, field_map.items)
    if not items:
        shape = (
            f"a {type(payload).__name__} with keys "
            f"{', '.join(sorted(map(str, payload))[:12]) or '<none>'}"
            if isinstance(payload, dict)
            else f"a {type(payload).__name__}"
        )
        return fail(
            EmbeddedJsonProblem.NO_ITEMS,
            f"{where}: <script id=\"{script_id}\"> decoded to {shape} and no list of "
            f"item objects was found at items={field_map.items!r}. The blob is still "
            "there and its shape changed, which is a different repair from a missing "
            "blob",
            seen=seen,
            blob_bytes=blob_bytes,
        )

    tally = _ItemTally()
    for item in items:
        _build_events(
            item, field_map=field_map, script_id=script_id, zone=zone, tally=tally
        )

    if not tally.events:
        return fail(
            EmbeddedJsonProblem.NO_DATES,
            f"{where}: {len(items)} item(s) in <script id=\"{script_id}\"> and not one "
            f"usable start date ({tally.undated} with an empty {field_map.starts!r} "
            f"array, {tally.bad_dates} unusable value(s)). Expected epoch seconds; a "
            "catalog of this size with zero dates is a schema change, not a quiet week",
            seen=seen,
            blob_bytes=blob_bytes,
            item_count=len(items),
        )

    notes: list[str] = []
    if tally.undated:
        notes.append(
            f"{tally.undated} of {len(items)} item(s) carry an empty "
            f"{field_map.starts!r} array and were skipped"
            + (f" ({', '.join(tally.undated_ids[:5])})" if tally.undated_ids else "")
        )
    if tally.bad_dates:
        notes.append(
            f"{tally.bad_dates} start value(s) were not a usable epoch and were "
            f"dropped (the literal 0 sentinel is the recorded case)"
        )
    if tally.missing_ids:
        notes.append(
            f"{tally.missing_ids} item(s) had no {field_map.uid!r} — their UID falls "
            "back to sha1(space + start + title) in normalize.py"
        )

    return _build(
        tally,
        item_count=len(items),
        script_id=script_id,
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


def _script_id_for(ref: SourceRef | None, override: str | None) -> str | None:
    """The blob name: the explicit argument, else the registry's ``script_id``."""
    if override:
        return override
    if ref is None:
        return None
    return ref.source.script_id


def parse_embedded_json(
    result: FetchResult,
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    today: dt.date | None = None,
    now: dt.datetime | None = None,
    strict_content_type: bool = True,
    script_id: str | None = None,
    field_map: FieldMap | None = None,
    ref: SourceRef | None = None,
    zone: str = DEFAULT_ZONE,
) -> EmbeddedJsonParse:
    """Parse a fetched page for its named JSON blob. Never raises.

    ``result`` is the registered page, already fetched by the caller. The blob
    name comes from ``script_id`` when given and otherwise from the registry
    entry (``ref.source.script_id``) — which is why
    :attr:`pipeline.cli.AdapterEntry.needs_source` exists: this adapter makes no
    further requests, so it needs the ``SourceRef`` without needing the
    ``Fetcher`` that ``tribe_rest`` and ``jsonld`` take.

    A 404, a rendered error page, a renamed blob, a truncated blob and a shape
    change all come back as an :class:`EmbeddedJsonParse` with ``ok=False`` and
    a distinct :class:`EmbeddedJsonProblem`.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    today = today or now.date()
    window_start = today
    window_end = today + dt.timedelta(days=horizon_days)
    where = f"{result.source_key} ({result.url})"
    name = _script_id_for(ref, script_id)

    transport = _transport_problem(result, where=where)
    if transport is not None:
        return _failure(
            transport[0],
            transport[1],
            space_id=result.space_id,
            label=result.label,
            source_url=result.url,
            script_id=name or "",
            horizon_days=horizon_days,
            window_start=window_start,
            window_end=window_end,
            now=now,
        )

    text = result.text
    if strict_content_type and result.content_type is not None:
        html_ok = result.content_type_is(*HTML_CONTENT_TYPES)
        if not html_ok:
            tolerable = result.content_type_is(*LENIENT_CONTENT_TYPES) or looks_like_html(text)
            if not tolerable:
                return _failure(
                    EmbeddedJsonProblem.WRONG_CONTENT_TYPE,
                    f"{where}: expected {' or '.join(HTML_CONTENT_TYPES)}, got "
                    f"{result.content_type} — HTTP 200 is not success, and a body that "
                    "is not the page we registered must not be read as a catalog with "
                    "nothing in it",
                    space_id=result.space_id,
                    label=result.label,
                    source_url=result.url,
                    script_id=name or "",
                    horizon_days=horizon_days,
                    window_start=window_start,
                    window_end=window_end,
                    now=now,
                )

    return _decode(
        text,
        script_id=name,
        field_map=field_map or CRUCIBLE_FIELD_MAP,
        where=where,
        space_id=result.space_id,
        label=result.label,
        source_url=result.final_url or result.url,
        horizon_days=horizon_days,
        window_start=window_start,
        window_end=window_end,
        now=now,
        zone=_zone(zone),
    )


def parse_embedded_json_text(
    document: str,
    *,
    script_id: str | None = None,
    field_map: FieldMap | None = None,
    space_id: str = "",
    label: str = "",
    source_url: str = "",
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    today: dt.date | None = None,
    now: dt.datetime | None = None,
    zone: str = DEFAULT_ZONE,
) -> EmbeddedJsonParse:
    """Parse **one HTML document**. The half with no HTTP in it.

    Split out for the same reason ``ics`` splits ``parse_ics_text``: the tests,
    and a human diffing two files out of ``raw/``, must be able to reach the
    parser without manufacturing a :class:`~pipeline.fetch.FetchResult`.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    today = today or now.date()
    return _decode(
        document,
        script_id=script_id,
        field_map=field_map or CRUCIBLE_FIELD_MAP,
        where=f"{space_id}:{label}" if space_id or label else "<embedded_json>",
        space_id=space_id,
        label=label,
        source_url=source_url,
        horizon_days=horizon_days,
        window_start=today,
        window_end=today + dt.timedelta(days=horizon_days),
        now=now,
        zone=_zone(zone),
    )


# --------------------------------------------------------------------------- fetching


def fetch_embedded_json(
    fetcher: Fetcher,
    ref: SourceRef,
    *,
    horizon_days: int | None = None,
    today: dt.date | None = None,
    now: dt.datetime | None = None,
    conditional: bool = False,
    strict_content_type: bool = True,
    script_id: str | None = None,
    field_map: FieldMap | None = None,
) -> EmbeddedJsonParse:
    """Fetch a registered source and parse it. All in one call.

    The counterpart to :func:`pipeline.adapters.jsonld.fetch_jsonld`, and the
    entry point for the repair workflow: one source, one page, no run.

    **Do not add a filter parameter to this URL.** It is the obvious next idea —
    the page is 1.8 MB and the UI has a department picker — and it does nothing.
    Verified 2026-08-05: ``/course-search/`` with no params, with
    ``?department=Blacksmithing``, with ``?department=ZZZ-NOT-A-DEPT`` and with
    ``?ac_department=blacksmithing`` all return the **identical** 216-course
    blob. There is no XHR endpoint either; the search UI filters this blob in
    client-side JavaScript, and the only ``admin-ajax.php`` references on the
    page belong to Popup Maker and Facebook-for-WooCommerce. Narrowing happens
    in ``sources.yaml``'s ``filters`` block, after the parse, or not at all.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    today = today or now.date()
    if horizon_days is None:
        horizon_days = fetcher.registry.horizon_days

    _space, source = ref
    result = fetcher.fetch_url(ref, request_url(source), conditional=conditional)
    return parse_embedded_json(
        result,
        horizon_days=horizon_days,
        today=today,
        now=now,
        strict_content_type=strict_content_type,
        script_id=script_id,
        field_map=field_map,
        ref=ref,
    )


#: The adapter entry point named by ``sources.yaml``'s ``adapter: embedded_json``.
parse = parse_embedded_json


__all__ = [
    "CRUCIBLE_FIELD_MAP",
    "DEFAULT_ZONE",
    "LENIENT_CONTENT_TYPES",
    "MAX_EPOCH",
    "MIN_EPOCH",
    "EmbeddedJsonEvent",
    "EmbeddedJsonParse",
    "EmbeddedJsonProblem",
    "FieldMap",
    "JsonScript",
    "fetch_embedded_json",
    "find_items",
    "find_script",
    "json_script_ids",
    "json_scripts",
    "looks_like_html",
    "parse",
    "parse_embedded_json",
    "parse_embedded_json_text",
    "parse_epoch",
    "parse_iso",
    "resolve_path",
]
