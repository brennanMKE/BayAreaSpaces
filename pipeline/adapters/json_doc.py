"""Standalone JSON document at a URL, mapped onto the intermediate event shape.

``sources.yaml`` names this adapter ``json``. **The module is
``json_doc.py``, not ``json.py``, on purpose:** a module named ``json`` inside
``pipeline/adapters/`` is not what ``import json`` resolves to under Python 3's
absolute imports, but it is what every reader assumes it is, and it makes
``from pipeline.adapters import json`` shadow the stdlib name at any call site
that also needs the real one. The dispatch table in :mod:`pipeline.cli` maps the
registry name to the function, so nothing about the registry changes. The stdlib
module is imported here as :mod:`json` and used only in :func:`_decode_body`.

Why this is a new adapter and not one of the six that already exist
-------------------------------------------------------------------

- ``tribe_rest`` (issue 0019) is The Events Calendar's *own* document shape:
  an object with an ``events`` array and a ``next_rest_url``. Neither consumer
  here has either.
- ``embedded_json`` (issue 0021) reads a **named ``<script>`` blob inside an
  HTML page**. This adapter fetches a JSON document *as* the response body.
  Everything the two genuinely share — :class:`~pipeline.adapters.embedded_json.FieldMap`,
  :func:`~pipeline.adapters.embedded_json.resolve_path`, the pluggable
  ``start_format``, the money and text helpers — is **imported** from there
  rather than copied. Two copies of ``_money`` would drift, and one of them
  would start turning "sliding scale $10-30" into a wrong number.
- ``jsonld`` is schema.org-specific and ``nextdata`` is Eventbrite-specific.

The registry says which shape a document has
--------------------------------------------

Two consumers, two shapes that have nothing in common, so a ``json`` source
carries ``shape:`` in ``sources.yaml`` exactly as an ``embedded_json`` source
carries ``script_id:``. Sniffing the shape out of the body would be a guess, and
a guess that happened to work once is worse than one that never did — see
:attr:`JsonProblem.NO_SHAPE`. :data:`SHAPES` is the whole vocabulary and
:mod:`pipeline.config` rejects any other value at load time.

Registered consumers (2026-08-05 survey)
----------------------------------------

``maker-nexus`` / ``amilia-community-events-cache`` — shape
``amilia_activities_cache``. ``storage.googleapis.com/makernexus_amilia_activities_cache/events.json``,
an **undocumented public cache** powering their ``/community-events`` page.
**30 forward-looking events, ``Last-Modified`` the same day**, so it is actively
maintained. ``trust: 90``.

``the-crucible`` / ``woocommerce-store-api`` — shape
``woocommerce_store_products``. ``/wp-json/wc/store/v1/products`` with
``per_page=100`` and ``orderby=date``. **353 products over 4 pages, 1531
``pa_class-date`` terms, 274 inside 120 days**, independently cross-validating
the ``embedded_json`` blob's 271. ``trust: 90``.

Maker Nexus: the key *is* the UID
----------------------------------

The document is a **dict**, not a list, keyed ``YYYY-MM-DD_<amiliaId>`` — a
ready-made stable identifier that already carries the occurrence date, which is
precisely what CLAUDE.md's UID rule asks for. :attr:`JsonFieldMap.keyed` says so,
and the key is used verbatim; ``normalize.py`` namespaces it to
``{space_id}:{key}``. Fields are ``Name``, ``Description``, ``Url``,
``CategoryName``, ``Price``, ``SpotsRemaining``, ``MaxAttendance``, and
``StartDate``/``EndDate`` as **ISO-8601 with a literal ``-07:00`` offset**.

The offset is kept exactly as written: it is an instant, and nothing here has to
guess. :attr:`~pipeline.adapters.ics.IcsEvent.tz` still carries the resolvable
IANA name :data:`~pipeline.adapters.embedded_json.DEFAULT_ZONE`, because
:func:`pipeline.normalize.day_start_utc` and issue 0016's health filter both call
``ZoneInfo`` on it and that filter runs *outside* the per-source try/except;
``source_tz`` carries the literal ``-07:00`` so the original is not lost.

This source is **richer than the same space's gCal** for the events they share,
so it wins the dedupe merge on ``price`` — already encoded as a
:class:`~pipeline.dedupe.FieldPreference` for
``maker-nexus``/``amilia-community-events-cache`` (issue 0015), and checked
against this module by ``tests/test_adapter_json.py``. Capacity
(``SpotsRemaining``) is the other documented preference and is **not expressible
yet**: the canonical record has no capacity field, so it is carried on
:attr:`JsonEvent.spots_remaining` for diagnostics and deliberately not declared
in ``dedupe.py``, where it would be validated and rejected.

**The GCS bucket cannot be enumerated anonymously** — ``storage.objects.list``
returns 401 — so ``events.json`` was found by name, and ``activities.json`` and
``camps.json`` were found by name-guessing and are **stale and dateless**
(catalog templates keyed by course name, ``Last-Modified`` 2025-12-19 and
2025-07-25, no ``StartDate`` field at all). Neither is registered and neither
should be. Probed and 404'd: ``classes.json``, ``class.json``, ``sessions.json``,
``calendar.json``, ``activities_cache.json``, ``all.json``, ``index.json``,
``youth.json``. If a classes cache exists, the way to get its name is to ask
staff, not to keep guessing.

The Crucible: ``orderby=date`` is load-bearing
-----------------------------------------------

**This is the most dangerous shape in the whole survey.** Without
``orderby=date`` the endpoint returns **HTTP 200, valid JSON, and
``X-WP-Total: 98`` instead of 353** — 72% of the catalog silently missing,
covering only 40 of the 216 live courses. Nothing about the response looks
wrong: right status, right content type, well-formed products, no error field
anywhere. It was caught only because two independent sources disagreed on the
count.

So the amputation is detected **twice, by two independent means**, and either
one fails the parse loudly rather than publishing a quietly smaller catalog:

1. :attr:`JsonShape.required_params` — the URL actually requested is inspected
   for ``orderby=date`` before the body is even read
   (:attr:`JsonProblem.MISSING_REQUIRED_PARAM`). This catches *our* mistake, at
   the moment it is made, and names the fix.
2. :attr:`Source.min_total <pipeline.config.Source.min_total>` — ``X-WP-Total``
   is read off the response and compared against the floor the registry
   declares (300, against the observed 353). A total below it is
   :attr:`JsonProblem.TRUNCATED_CATALOG`. This does not trust check 1: the
   parameter can be present and the *server* can change its mind, and a
   catalog that shrinks by 72% overnight is not a quiet week.
   A declared floor with **no ``X-WP-Total`` header at all** is
   :attr:`JsonProblem.NO_TOTAL` — losing the guard is itself the failure,
   because a source we can no longer check is not a source we can trust.

**Pagination is on ``X-WP-TotalPages``, never until-error.** ``page=99`` returns
**200 with a body of ``[]``**, not an error, so an until-error walk would spin
to :data:`MAX_PAGES` and call it a healthy feed. The page count is read from the
header, pages 2..n are requested with the *same* query string plus ``page=N``
(so ``orderby=date`` and ``per_page`` survive to every page), and an empty list
arriving *before* the declared last page stops the walk and is reported as a
note rather than swallowed.

**Dates live in ``attributes[].terms[].name``** as ``MM/DD/YY H:MM am/pm``,
local. One product carries one term per offering, which is why 353 products
yield 1531 terms.

**The literal sentinel ``01/01/70 12:00 am`` is dropped and counted.** Two terms
carry it. It is not merely out of horizon: ``dateutil`` resolves a two-digit
``70`` against a window centred on *this* year, so it lands in **2070**, and
2070 sails past every horizon check and every staleness gate without erroring.
:data:`EPOCH_SENTINEL` is therefore matched as text and dropped *before* any
parse, and :data:`MIN_YEAR`/:data:`MAX_YEAR` catch anything else of the same
kind. ``tests/test_adapter_json.py`` pins the 2070 behavior so the reason this
code exists cannot quietly stop being true.

**The Store API's own ``categories`` are not published.** 60+ of the 118
distinct category names are one-off per-product categories *equal to the class
title* (``Wheel Throwing II``, ``Youth Kandi Cuffs and Perlers``, …). Issue
0010's ``categories_exclude`` runs on ``Event.categories``, and dedupe (issue
0015) already says the ``embedded_json`` blob's ``department`` wins that field
and that this source "must not supply the field even when the blob is empty".
:attr:`JsonFieldMap.categories` is therefore ``None`` for this shape and the raw
list is carried on :attr:`JsonEvent.source_categories` for diagnostics only.

**No location, on purpose.** The catalog carries no location field of any kind —
not in the blob, not in the Store API, not in the product HTML. ``location`` is
left ``None`` so :class:`pipeline.normalize.VenuePolicy` applies the space's
mandatory ``address_override``.

Implemented by issue 0022.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from pipeline.adapters.embedded_json import (
    DEFAULT_ZONE,
    LENIENT_CONTENT_TYPES,
    FieldMap,
    looks_like_html,
    parse_epoch,
    parse_iso,
    resolve_path,
)

# The text helpers are ``embedded_json``'s, imported rather than copied. They
# are private by name because nothing outside the adapters should call them —
# but a second copy of ``_money`` in this file would drift from the first, and
# the copy that drifted would be the one that started parsing "sliding scale
# $10-30" into a number.
from pipeline.adapters.embedded_json import (  # noqa: PLC2701
    _bool as as_bool,
)
from pipeline.adapters.embedded_json import (  # noqa: PLC2701
    _clean as clean,
)
from pipeline.adapters.embedded_json import (  # noqa: PLC2701
    _money as money,
)
from pipeline.adapters.embedded_json import (  # noqa: PLC2701
    _strings as strings,
)
from pipeline.adapters.ics import DEFAULT_HORIZON_DAYS, IcsEvent, IcsParse
from pipeline.config import SourceRef
from pipeline.fetch import (
    JSON_CONTENT_TYPES,
    FetchResult,
    Fetcher,
    Outcome,
    request_url,
)

# --------------------------------------------------------------------------- knobs

#: The literal Store API sentinel, matched as **text** and dropped before any
#: parser sees it. Two ``pa_class-date`` terms carry it verbatim.
#:
#: Dropping it explicitly rather than letting a horizon check catch it is the
#: whole point: ``dateutil`` resolves the two-digit ``70`` against a window
#: centred on the current year, so this string becomes **2070-01-01**, which is
#: inside no horizon and outside no sanity window that anybody wrote down.
EPOCH_SENTINEL = "01/01/70 12:00 am"

#: Years outside this range are rejected as sentinels rather than published. The
#: 2070 case above is the recorded one; a source that starts emitting 1970 (the
#: ``strptime`` reading of the same string) lands here too.
MIN_YEAR = 1990
MAX_YEAR = 2040

#: ``MM/DD/YY H:MM am/pm``, local. Non-padded hours are the norm in this data
#: ("08/06/26 6:00 pm"), and ``%I`` accepts both. Parsed with an explicit format
#: rather than with ``dateutil``: the format is known and fixed, and a
#: heuristic parser is exactly what turns the sentinel into 2070.
US_SHORT_FORMATS: tuple[str, ...] = ("%m/%d/%y %I:%M %p", "%m/%d/%Y %I:%M %p")

#: Hard ceiling on pages per source, whatever the headers claim. At
#: ``per_page=100`` this is 2000 products, five times the observed 353. It is
#: not the stop condition — :attr:`JsonShape.total_pages_header` is — it is the
#: guard against a header that says 900.
MAX_PAGES = 20

#: How far into a body to look before deciding what it is.
_SNIFF_BYTES = 4096

#: Crude tag stripper for the WooCommerce ``description``/``short_description``,
#: which are stored as HTML. Deliberately not a parser: the target is a ~300
#: character summary, the input is ``<p>`` and ``<br>``, and pulling lxml in to
#: read one field would be heavier than the field.
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


# --------------------------------------------------------------------------- outcomes


class JsonProblem(str, Enum):
    """Why a :class:`JsonParse` is not usable. ``NONE`` means it is.

    The first five mirror :class:`~pipeline.adapters.ics.IcsProblem` so the
    CLI's ``record.problem`` stays one vocabulary. The rest are the failures
    only a standalone JSON document can have, and they are kept apart because
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
    #: **No ``shape`` was configured.** A configuration error, not a source
    #: failure: the two registered documents have nothing in common, and
    #: sniffing which one arrived would be a guess.
    NO_SHAPE = "no_shape"
    #: A ``shape`` was configured and is not in :data:`SHAPES`.
    UNKNOWN_SHAPE = "unknown_shape"
    #: The body is not JSON — the 200-with-a-rendered-page case.
    NOT_JSON = "not_json"
    #: Valid JSON, and no list (or keyed dict) of item objects in it. The shape
    #: changed under the same URL.
    NO_ITEMS = "no_items"
    #: Items were found and **not one** carried a usable date.
    NO_DATES = "no_dates"

    #: **The load-bearing query parameter was not on the request.** The Crucible
    #: without ``orderby=date`` returns 200, valid JSON, and 98 of 353 products.
    MISSING_REQUIRED_PARAM = "missing_required_param"
    #: The source reported fewer items than the registry's ``min_total``. The
    #: same amputation, caught from the response rather than the request.
    TRUNCATED_CATALOG = "truncated_catalog"
    #: A ``min_total`` is declared and the total header is absent, so the guard
    #: above cannot run at all.
    NO_TOTAL = "no_total"

    #: Page 1 was fine; a later page failed. Partial data is never returned as
    #: though it were whole.
    PAGE_FAILED = "page_failed"
    #: More pages were offered than :data:`MAX_PAGES`.
    RUNAWAY_PAGINATION = "runaway_pagination"


# --------------------------------------------------------------------------- shapes


@dataclass(frozen=True)
class JsonFieldMap(FieldMap):
    """:class:`~pipeline.adapters.embedded_json.FieldMap` plus what a standalone
    document needs.

    Every inherited value is still a **dotted path** resolved by
    :func:`~pipeline.adapters.embedded_json.resolve_path`.
    """

    #: The document is a **dict keyed by identifier**, and the key is the UID.
    #: Maker Nexus's ``YYYY-MM-DD_<amiliaId>``.
    keyed: bool = False

    #: Both overridden to allow ``None``, which is not the same as "absent":
    #: for the WooCommerce shape ``categories=None`` means *do not publish this
    #: source's categories at all* (see the module docstring), and
    #: ``price=None`` means the price is not at a dotted path but in the
    #: minor-unit object :func:`woo_price_text` reads.
    categories: str | None = "department"  # type: ignore[assignment]
    price: str | None = "price"  # type: ignore[assignment]

    #: How to get from the item to its start values. ``"value"`` resolves
    #: :attr:`~pipeline.adapters.embedded_json.FieldMap.starts` directly;
    #: ``"woo_terms"`` walks ``attributes[]`` for :attr:`terms_taxonomy` and
    #: yields every ``terms[].name``.
    #:
    #: ``start_format`` gains one value alongside the inherited
    #: ``"epoch_seconds"`` / ``"epoch_millis"`` / ``"iso"``: ``"us_short"``, for
    #: The Crucible's ``MM/DD/YY H:MM am/pm``.
    start_container: str = "value"

    #: The end instant, when the source has one. Parsed with
    #: :attr:`~pipeline.adapters.embedded_json.FieldMap.start_format`. Only
    #: consulted when the item carries exactly one start — a multi-offering item
    #: has no per-offering end anywhere in either document.
    end: str | None = None

    #: Which ``attributes[].taxonomy`` carries the dates.
    terms_taxonomy: str = "pa_class-date"

    #: Path *within* one term to its stable id. Half of the WooCommerce UID.
    term_uid: str | None = "id"

    #: ``"text"`` (the inherited behavior) or ``"woo_prices"`` for WooCommerce's
    #: minor-unit ``prices`` object.
    price_kind: str = "text"

    spots_remaining: str | None = None
    max_attendance: str | None = None

    #: Text fields to run through :func:`strip_tags` on the way out. The
    #: WooCommerce descriptions are stored as HTML.
    html_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class JsonShape:
    """One named document shape: the field map plus its transport policy.

    Named rather than inferred, and enumerated in :data:`SHAPES`, so
    ``sources.yaml`` can only ever name one that exists.
    """

    name: str
    field_map: JsonFieldMap
    #: The IANA zone the document's local times are in, and the resolvable name
    #: every timed event carries in ``tz``.
    zone: str = DEFAULT_ZONE

    #: Query parameters that **must** be on the request, as ``(name, value)``.
    #: The Crucible's ``orderby=date`` is the recorded case and the reason this
    #: field exists.
    required_params: tuple[tuple[str, str], ...] = ()

    #: Response headers carrying the item total and the page count. ``None``
    #: means the shape is a single document with no pagination.
    total_header: str | None = None
    total_pages_header: str | None = None
    page_param: str = "page"

    #: Values dropped as sentinels **before** parsing, matched case-insensitively
    #: on the trimmed text.
    drop_values: tuple[str, ...] = ()

    note: str = ""

    @property
    def paginated(self) -> bool:
        return self.total_pages_header is not None


#: Maker Nexus's Amilia cache. One document, no pagination, dict-keyed.
AMILIA_ACTIVITIES_CACHE = JsonShape(
    name="amilia_activities_cache",
    field_map=JsonFieldMap(
        keyed=True,
        title="Name",
        url="Url",
        description="Description",
        categories="CategoryName",
        price="Price",
        member_price=None,
        image=None,
        in_stock=None,
        hours=None,
        audience=None,
        level=None,
        item_format=None,
        time_of_day=None,
        days_text=None,
        location=None,
        starts="StartDate",
        start_format="iso",
        end="EndDate",
        spots_remaining="SpotsRemaining",
        max_attendance="MaxAttendance",
    ),
    note=(
        "Undocumented public GCS object powering /community-events. 30 "
        "forward-looking events; the bucket cannot be enumerated anonymously."
    ),
)

#: The Crucible's WooCommerce Store API v1 product listing.
WOOCOMMERCE_STORE_PRODUCTS = JsonShape(
    name="woocommerce_store_products",
    field_map=JsonFieldMap(
        uid="id",
        title="name",
        url="permalink",
        description="short_description",
        # DELIBERATELY None. See the module docstring: 60+ of this endpoint's
        # categories are one-off per-product entries equal to the class title,
        # and dedupe already says the blob's `department` owns this field.
        categories=None,
        price=None,
        price_kind="woo_prices",
        member_price=None,
        image=None,
        in_stock="is_in_stock",
        hours=None,
        audience=None,
        level=None,
        item_format=None,
        time_of_day=None,
        days_text=None,
        location=None,
        starts="attributes",
        start_container="woo_terms",
        start_format="us_short",
        html_fields=("description",),
    ),
    required_params=(("orderby", "date"),),
    total_header="x-wp-total",
    total_pages_header="x-wp-totalpages",
    drop_values=(EPOCH_SENTINEL,),
    note=(
        "353 products over 4 pages with orderby=date; 98 over 1 page without "
        "it, at HTTP 200 with valid JSON."
    ),
)

#: The whole vocabulary ``sources.yaml`` may name. ``pipeline.config`` validates
#: against these keys, so a typo is a load-time error and never a quiet zero.
SHAPES: dict[str, JsonShape] = {
    AMILIA_ACTIVITIES_CACHE.name: AMILIA_ACTIVITIES_CACHE,
    WOOCOMMERCE_STORE_PRODUCTS.name: WOOCOMMERCE_STORE_PRODUCTS,
}


# --------------------------------------------------------------------------- events


@dataclass(frozen=True)
class JsonEvent(IcsEvent):
    """One offering, in the intermediate shape the ``ics`` adapter produces.

    A subclass rather than a parallel class, for the same reason
    :class:`~pipeline.adapters.tribe_rest.TribeEvent` is one: ``normalize.py``
    consumes every adapter through :func:`~pipeline.normalize.from_ics_event`,
    and the moment a second shape appears one of them starts quietly losing
    fields.
    """

    #: The shape this came out of. Provenance for a two-consumer adapter.
    shape: str = ""
    #: The document key for a keyed shape — and, for Maker Nexus, the UID itself.
    item_key: str | None = None
    #: The source's own item id, as text.
    item_id: str | None = None
    #: The WooCommerce term id for this offering's date. The other half of that
    #: shape's UID, and the reason it is stable across runs.
    term_id: str | None = None
    #: The epoch second the offering starts at.
    start_epoch: int | None = None

    #: Price as **source text**, never a float. "sliding scale $10-30" and
    #: "free for members" are real values in this dataset.
    cost: str | None = None
    member_cost: str | None = None

    #: ``SpotsRemaining`` / ``MaxAttendance``. Carried for diagnostics: the
    #: canonical record has no capacity field yet, so dedupe cannot express the
    #: documented "this source wins capacity" preference. See dedupe.py.
    spots_remaining: int | None = None
    max_attendance: int | None = None

    in_stock: bool = True
    image: str | None = None

    #: The source's own categories when they are deliberately **not** published
    #: as :attr:`~pipeline.adapters.ics.IcsEvent.categories`. Diagnostics only.
    source_categories: tuple[str, ...] = ()

    #: Which page of a paginated response this came from. 1-based.
    page: int = 1

    @property
    def price(self) -> str | None:
        """Alias :attr:`cost` under the canonical schema's name.

        :func:`pipeline.normalize.from_ics_event` reads ``price`` off whatever
        the adapter handed it.
        """
        return self.cost


# --------------------------------------------------------------------------- pages


@dataclass(frozen=True)
class JsonPage:
    """One decoded page of a document.

    Its own object so pagination is testable without HTTP and so the parse can
    report *which* page went wrong.
    """

    url: str
    number: int = 1
    #: ``(key, item)`` pairs. ``key`` is ``None`` for a list document.
    items: tuple[tuple[str | None, dict[str, Any]], ...] = ()
    total: int | None = None
    total_pages: int | None = None
    byte_count: int = 0

    def __len__(self) -> int:
        return len(self.items)


# --------------------------------------------------------------------------- result


@dataclass(frozen=True)
class JsonParse(IcsParse):
    """The adapter's return value. Same surface as :class:`IcsParse`, plus the
    document.

    Subclasses :class:`~pipeline.adapters.ics.IcsParse` so every consumer
    written against the ICS adapter — ``normalize_ics``, the CLI's
    ``SourceRecord``, issue 0016's gates — works unchanged.
    """

    problem: JsonProblem = JsonProblem.NONE  # type: ignore[assignment]

    #: The shape we were sent for.
    shape: str = ""

    #: ``X-WP-Total`` as the source reported it: 353 for The Crucible with
    #: ``orderby=date``, **98 without it**.
    reported_total: int | None = None
    #: ``X-WP-TotalPages``: 4.
    reported_pages: int | None = None
    #: The floor the registry declared for :attr:`reported_total`.
    expected_min_total: int | None = None
    #: One entry per page actually read, in order.
    pages: tuple[JsonPage, ...] = ()
    #: True when more pages were on offer and deliberately not followed — a
    #: single-page call with no fetcher. Never true inside a nightly run.
    truncated: bool = False

    #: Item objects seen across every page. 353 products / 30 cache entries.
    item_count: int = 0
    #: Items whose start path was absent or empty. Counted, never silent.
    undated_item_count: int = 0
    undated_ids: tuple[str, ...] = ()
    #: Start values present and unusable: a string that is not a date, a year
    #: outside :data:`MIN_YEAR`..:data:`MAX_YEAR`.
    bad_date_count: int = 0
    #: **Values dropped as literal sentinels.** Two, for The Crucible.
    sentinel_count: int = 0
    #: Items with no usable id. Their UID falls back to normalize's sha1.
    missing_id_count: int = 0
    #: ``sha1(page 1 body)[:16]``, for diffing ``raw/`` against a claim.
    document_digest: str = ""
    document_bytes: int = 0

    @property
    def ok(self) -> bool:
        """True when the document decoded, the guards passed, and it held dated
        items."""
        return self.problem is JsonProblem.NONE

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def offering_count(self) -> int:
        """Raw start values seen across every item: 1531 ``pa_class-date`` terms
        for The Crucible.

        The same number as :attr:`~pipeline.adapters.ics.IcsParse.vevent_count`,
        under the name the research file uses.
        """
        return self.vevent_count

    @property
    def counts_agree(self) -> bool:
        """Whether the decoded item count matches the reported total.

        Not a gate — a note. The gate is :attr:`expected_min_total`, because
        "fewer than the registry expects" is the failure that actually happened
        and "one off the reported total" is a paging artifact.
        """
        return self.reported_total is None or self.reported_total == self.item_count

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        head = f"{self.space_id}:{self.label}" if self.space_id or self.label else "<json>"
        if not self.ok:
            return f"{head} FAILED {self.problem.value}: {self.error}"
        return (
            f"{head} {self.event_count} events in {self.horizon_days}d "
            f"(from {self.item_count} items / {self.offering_count} offerings "
            f"across {self.page_count} page(s), shape={self.shape})"
        )


# --------------------------------------------------------------------------- text


def strip_tags(value: Any) -> str | None:
    """``"<p>Learn to weld.</p>"`` -> ``"Learn to weld."``.

    For the WooCommerce descriptions, which are stored as HTML. Entities are
    decoded by :func:`clean` on the way through, as everywhere else in the
    adapters.
    """
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    # Tags first, entities second. Unescaping first would turn a literal
    # ``&lt;p&gt;`` in someone's course description into a tag and then eat it.
    stripped = clean(_TAG_RE.sub(" ", str(value)))
    if stripped is None:
        return None
    return _WS_RE.sub(" ", stripped).strip() or None


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def looks_like_json_document(text: str) -> bool:
    """True if the body opens with ``{`` or ``[``.

    The JSON half of "HTTP 200 is not success". Both registered documents are
    top-level containers — a dict for Maker Nexus, a list for WooCommerce — so
    either opener is legitimate here and a ``<`` is not.
    """
    return text[:_SNIFF_BYTES].lstrip("﻿ \t\r\n").startswith(("{", "["))


# --------------------------------------------------------------------------- money


def woo_price_text(prices: Any, *, symbol_default: str = "$") -> str | None:
    """WooCommerce's minor-unit ``prices`` object as **source text**.

    ``{"price": "12500", "currency_minor_unit": 2, "currency_symbol": "$"}``
    becomes ``"$125"``; a variable product's ``price_range`` becomes
    ``"$95 - $125"``. Nothing downstream ever sees a float — same rule as
    ``tribe_rest``, ``jsonld`` and ``embedded_json``, for the same reason.
    """
    if not isinstance(prices, dict):
        return None
    minor = _int(prices.get("currency_minor_unit"))
    symbol = clean(prices.get("currency_symbol")) or symbol_default

    def amount(raw: Any) -> str | None:
        text = clean(raw)
        if text is None:
            return None
        try:
            value = float(text)
        except ValueError:
            # Not a number. Keep whatever the source wrote rather than dropping
            # it: "Call for pricing" is a real thing for a source to say.
            return text
        if minor:
            value /= 10**minor
        rendered = f"{value:,.2f}"
        if rendered.endswith(".00"):
            rendered = rendered[:-3]
        return f"{symbol}{rendered}"

    span = prices.get("price_range")
    if isinstance(span, dict):
        low = amount(span.get("min_amount"))
        high = amount(span.get("max_amount"))
        if low and high and low != high:
            return f"{low} - {high}"
        if low or high:
            return low or high
    return amount(prices.get("price"))


# --------------------------------------------------------------------------- dates


def _zone(name: str) -> ZoneInfo | None:
    try:
        return ZoneInfo(name)
    except Exception:  # pragma: no cover - tzdata missing
        return None


def parse_us_short(
    value: Any, *, zone: ZoneInfo | None
) -> tuple[dt.datetime | None, int | None]:
    """``"08/06/26 6:00 pm"`` -> ``2026-08-06 18:00:00-07:00``, plus its epoch.

    Parsed with :data:`US_SHORT_FORMATS` rather than with ``dateutil``, and the
    difference is not stylistic. ``dateutil`` resolves a two-digit year against
    a window centred on the current year, which turns the Store API's
    ``01/01/70 12:00 am`` sentinel into **2070** — a date inside no horizon and
    outside no sanity window, which is exactly how it survives to publication.
    That string is dropped as text before this function is reached
    (:data:`EPOCH_SENTINEL`); :data:`MIN_YEAR`/:data:`MAX_YEAR` here are the
    second net, for the sentinel nobody has met yet.

    Returns ``(None, None)`` for anything unusable. A value with no zone is read
    as local to ``zone``, because a source writing wall-clock strings means the
    venue's wall clock; nothing naive ever leaves this module.
    """
    text = clean(value)
    if not text:
        return None, None
    parsed: dt.datetime | None = None
    for fmt in US_SHORT_FORMATS:
        try:
            parsed = dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
        break
    if parsed is None:
        return None, None
    if not MIN_YEAR <= parsed.year <= MAX_YEAR:
        return None, None
    target = zone or _zone(DEFAULT_ZONE)
    if target is None:  # pragma: no cover - tzdata missing
        return None, None
    aware = parsed.replace(tzinfo=target)
    return aware, int(aware.timestamp())


def parse_start(
    value: Any, *, start_format: str, zone: ZoneInfo | None
) -> tuple[dt.datetime | None, int | None]:
    """Dispatch one raw start value on the shape's ``start_format``."""
    if start_format == "iso":
        parsed, epoch = parse_iso(value, zone=zone)
    elif start_format == "us_short":
        parsed, epoch = parse_us_short(value, zone=zone)
    else:
        parsed, epoch = parse_epoch(
            value, zone=zone, millis=start_format == "epoch_millis"
        )
    if parsed is None or epoch is None:
        return None, None
    if not MIN_YEAR <= parsed.year <= MAX_YEAR:
        return None, None
    return parsed, epoch


def iso_offset(value: Any) -> str | None:
    """The literal offset an ISO-8601 string carried: ``"-07:00"``.

    Kept on :attr:`~pipeline.adapters.ics.IcsEvent.source_tz` so the source's own
    words survive alongside the resolvable IANA name ``tz`` has to carry.
    """
    text = clean(value)
    if not text:
        return None
    if text.endswith(("Z", "z")):
        return "UTC"
    tail = text[-6:]
    if len(tail) == 6 and tail[0] in "+-" and tail[3] == ":":
        return tail
    return None


# --------------------------------------------------------------------------- items


def document_items(
    payload: Any, field_map: JsonFieldMap
) -> list[tuple[str | None, dict[str, Any]]]:
    """``(key, item)`` pairs out of a decoded document.

    A **keyed** shape requires a dict of objects and yields its keys, because
    the key is the identity: Maker Nexus's ``2026-08-06_1234567`` already
    carries the occurrence date, which is what makes it a UID and not just an
    index. A list shape yields ``(None, item)``. Anything else yields nothing,
    which the caller reports as :attr:`JsonProblem.NO_ITEMS` — the shape changed
    under the same URL, and that is a different repair from an empty catalog.
    """
    if field_map.keyed:
        if not isinstance(payload, dict):
            return []
        return [
            (str(key), value)
            for key, value in payload.items()
            if isinstance(value, dict)
        ]
    if field_map.items:
        payload = resolve_path(payload, field_map.items)
    if isinstance(payload, list):
        return [(None, entry) for entry in payload if isinstance(entry, dict)]
    return []


def woo_term_values(
    attributes: Any, *, taxonomy: str, term_uid: str | None
) -> list[tuple[str | None, Any]]:
    """``attributes[].terms[].name`` for one taxonomy, with each term's id.

    The Crucible's dates live nowhere else: a class is a WooCommerce *variable
    product* whose variations are keyed on a ``pa_class-date`` attribute, and
    the attribute's terms are the offerings. 353 products carry 1531 of them.

    Matching is on ``taxonomy`` and falls back to the human ``name`` (``"Class
    Date"``) so a site that registers the attribute as non-taxonomy still works.
    """
    if not isinstance(attributes, list):
        return []
    wanted = taxonomy.strip().lower()
    human = wanted.removeprefix("pa_").replace("-", " ").replace("_", " ")
    out: list[tuple[str | None, Any]] = []
    for attribute in attributes:
        if not isinstance(attribute, dict):
            continue
        name = (clean(attribute.get("name")) or "").lower()
        declared = (clean(attribute.get("taxonomy")) or "").lower()
        if declared != wanted and name != human:
            continue
        terms = attribute.get("terms")
        if not isinstance(terms, list):
            continue
        for term in terms:
            if isinstance(term, dict):
                out.append((clean(resolve_path(term, term_uid)), term.get("name")))
            else:
                out.append((None, term))
    return out


def start_values(
    item: dict[str, Any], field_map: JsonFieldMap
) -> list[tuple[str | None, Any]]:
    """Every ``(term_id, raw start)`` an item carries, in document order."""
    raw = resolve_path(item, field_map.starts)
    if field_map.start_container == "woo_terms":
        return woo_term_values(
            raw, taxonomy=field_map.terms_taxonomy, term_uid=field_map.term_uid
        )
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [(None, entry) for entry in raw]
    return [(None, raw)]


# --------------------------------------------------------------------------- building


def _sort_key(event: JsonEvent) -> tuple[dt.date, int, dt.time, str]:
    """Same ordering rule as the ICS adapter: day, all-day first, then time."""
    start = event.start
    if isinstance(start, dt.datetime):
        moment = start.astimezone(dt.timezone.utc) if start.tzinfo else start
        return (moment.date(), 1, moment.time(), event.uid or "")
    return (start, 0, dt.time.min, event.uid or "")


@dataclass
class _Tally:
    """Internal: what the item walk saw, so the counts are not recomputed."""

    items: int = 0
    offerings: int = 0
    undated: int = 0
    undated_ids: list[str] = field(default_factory=list)
    bad_dates: int = 0
    sentinels: int = 0
    missing_ids: int = 0
    events: list[JsonEvent] = field(default_factory=list)


def _text(item: dict[str, Any], path: str | None, field_map: JsonFieldMap, name: str) -> str | None:
    """One text field, tag-stripped when the shape says it is HTML."""
    value = resolve_path(item, path)
    return strip_tags(value) if name in field_map.html_fields else clean(value)


def _price_text(item: dict[str, Any], field_map: JsonFieldMap) -> tuple[str | None, str | None]:
    """``(price, member_price)`` as source text."""
    if field_map.price_kind == "woo_prices":
        return woo_price_text(item.get("prices")), None
    cost = money(resolve_path(item, field_map.price))
    member = money(resolve_path(item, field_map.member_price))
    return cost, member


def _build_events(
    key: str | None,
    item: dict[str, Any],
    *,
    shape: JsonShape,
    zone: ZoneInfo | None,
    page: int,
    tally: _Tally,
) -> None:
    """One item -> **one event per start value**, appended to ``tally``."""
    field_map = shape.field_map
    tally.items += 1

    item_id = key if field_map.keyed else clean(resolve_path(item, field_map.uid))
    if item_id is None:
        tally.missing_ids += 1

    raw_starts = start_values(item, field_map)
    if not raw_starts:
        # Counted, never emitted: normalize would drop a dateless event as
        # NO_START anyway, and a silently smaller catalog is the failure this
        # project keeps designing against.
        tally.undated += 1
        if item_id:
            tally.undated_ids.append(item_id)
        return

    title = _text(item, field_map.title, field_map, "title")
    url = clean(resolve_path(item, field_map.url))
    description = _text(item, field_map.description, field_map, "description")
    raw_categories = strings(resolve_path(item, field_map.categories))
    # `categories=None` means "this source must not supply the field" — see the
    # module docstring on The Crucible's polluted WooCommerce categories.
    hidden_categories = (
        strings(resolve_path(item, "categories")) if field_map.categories is None else ()
    )
    cost, member_cost = _price_text(item, field_map)
    in_stock = as_bool(resolve_path(item, field_map.in_stock))
    spots = _int(resolve_path(item, field_map.spots_remaining))
    capacity = _int(resolve_path(item, field_map.max_attendance))

    price_text = cost
    if cost and member_cost and member_cost != cost:
        price_text = f"{cost} / {member_cost} members"

    single = len(raw_starts) == 1
    drop = {value.strip().lower() for value in shape.drop_values}

    for term_id, raw_start in raw_starts:
        tally.offerings += 1

        # The sentinel, dropped as **text**, before any parser sees it. This is
        # the whole reason the count below exists: `01/01/70 12:00 am` parses
        # cleanly, lands in 2070 under dateutil, and passes every check after
        # this one.
        as_text = clean(raw_start)
        if as_text is not None and as_text.strip().lower() in drop:
            tally.sentinels += 1
            continue

        start, epoch = parse_start(
            raw_start, start_format=field_map.start_format, zone=zone
        )
        if start is None or epoch is None:
            tally.bad_dates += 1
            continue

        end: dt.datetime | None = None
        if field_map.end and single:
            end, _ = parse_start(
                resolve_path(item, field_map.end),
                start_format=field_map.start_format,
                zone=zone,
            )
            if end is not None and end <= start:
                end = None

        if item_id is None:
            uid = None
        elif field_map.keyed:
            # The document key verbatim. It already carries the date, so it is
            # a stable identifier on its own; only a key with several starts
            # (none observed) needs disambiguating.
            uid = item_id if single else f"{item_id}:{epoch}"
        elif term_id:
            # {product_id}:{term_id} — both are WordPress ids, so this is stable
            # across runs by construction, and it is deliberately *not* the
            # embedded_json blob's {id}:{start_epoch}: the two Crucible sources
            # are meant to meet in dedupe on title and start, resolved by trust,
            # rather than to be silently identical.
            uid = f"{item_id}:{term_id}"
        else:
            uid = f"{item_id}:{epoch}"

        tally.events.append(
            JsonEvent(
                uid=uid,
                title=title,
                start=start,
                end=end,
                all_day=False,
                multi_day=False,
                days=1,
                # A resolvable IANA name, not an offset: normalize.day_start_utc
                # and issue 0016's health filter both call ZoneInfo on this, and
                # that filter runs outside the per-source try/except.
                tz=shape.zone if zone is not None else None,
                # The source's own words, when it said any. "-07:00" for Maker
                # Nexus; None for a wall-clock string, because there the zone is
                # our stated policy rather than the document's claim.
                source_tz=iso_offset(raw_start) if field_map.start_format == "iso" else None,
                dtstart_form="tzid" if field_map.start_format == "iso" else "local",
                # No location field exists in either document. Leaving it None
                # hands the decision to VenuePolicy and the space's
                # address_override, which is mandatory for The Crucible.
                location=clean(resolve_path(item, field_map.location)),
                description=description,
                url=url,
                categories=raw_categories,
                status=None,
                organizer=None,
                recurring=False,
                last_modified=None,
                dtstamp=None,
                shape=shape.name,
                item_key=key,
                item_id=item_id,
                term_id=term_id,
                start_epoch=epoch,
                cost=price_text,
                member_cost=member_cost,
                spots_remaining=spots,
                max_attendance=capacity,
                in_stock=in_stock,
                image=clean(resolve_path(item, field_map.image)),
                source_categories=hidden_categories,
                page=page,
            )
        )


def _build(
    tally: _Tally,
    pages: Sequence[JsonPage],
    *,
    shape: str,
    space_id: str,
    label: str,
    source_url: str,
    horizon_days: int,
    window_start: dt.date,
    window_end: dt.date,
    now: dt.datetime,
    reported_total: int | None = None,
    reported_pages: int | None = None,
    expected_min_total: int | None = None,
    document_digest: str = "",
    document_bytes: int = 0,
    truncated: bool = False,
    problem: JsonProblem = JsonProblem.NONE,
    error: str | None = None,
) -> JsonParse:
    """Assemble the walk into the returned :class:`JsonParse`.

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
    return JsonParse(
        events=tuple(sorted(kept, key=_sort_key)),
        problem=problem,
        error=error,
        space_id=space_id,
        label=label,
        source_url=source_url,
        # Raw = offerings, i.e. every start value seen: 1531 pa_class-date terms
        # for The Crucible. The health gates count `event_count`.
        vevent_count=tally.offerings,
        recurring_vevent_count=0,
        horizon_days=horizon_days,
        window_start=window_start,
        window_end=window_end,
        parsed_at=now,
        shape=shape,
        reported_total=reported_total,
        reported_pages=reported_pages,
        expected_min_total=expected_min_total,
        pages=tuple(pages),
        truncated=truncated,
        item_count=tally.items,
        undated_item_count=tally.undated,
        undated_ids=tuple(tally.undated_ids[:20]),
        bad_date_count=tally.bad_dates,
        sentinel_count=tally.sentinels,
        missing_id_count=tally.missing_ids,
        document_digest=document_digest,
        document_bytes=document_bytes,
    )


def _failure(
    problem: JsonProblem,
    error: str,
    *,
    space_id: str = "",
    label: str = "",
    source_url: str = "",
    shape: str = "",
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    window_start: dt.date | None = None,
    window_end: dt.date | None = None,
    now: dt.datetime | None = None,
    reported_total: int | None = None,
    reported_pages: int | None = None,
    expected_min_total: int | None = None,
    pages: Sequence[JsonPage] = (),
    item_count: int = 0,
) -> JsonParse:
    """A reported failure. Never raised — see the ``ics`` module docstring."""
    return JsonParse(
        problem=problem,
        error=error,
        space_id=space_id,
        label=label,
        source_url=source_url,
        shape=shape,
        horizon_days=horizon_days,
        window_start=window_start,
        window_end=window_end,
        parsed_at=now,
        reported_total=reported_total,
        reported_pages=reported_pages,
        expected_min_total=expected_min_total,
        pages=tuple(pages),
        item_count=item_count,
    )


# --------------------------------------------------------------------------- decoding


@dataclass(frozen=True)
class _PageResult:
    """Internal: one decoded page, or the reason it could not be decoded."""

    page: JsonPage | None = None
    problem: JsonProblem = JsonProblem.NONE
    error: str | None = None
    tolerated: str | None = None
    digest: str = ""


def _transport_problem(result: FetchResult, *, where: str) -> tuple[JsonProblem, str] | None:
    """Everything that can be wrong before the body is worth looking at."""
    if result.outcome is Outcome.NOT_MODIFIED:
        return (
            JsonProblem.NOT_MODIFIED,
            f"{where}: HTTP 304, unchanged since the last run — reuse the stored "
            "events rather than treating this as zero",
        )
    if result.outcome is not Outcome.FETCHED:
        detail = f" ({result.reason})" if result.reason else ""
        return (JsonProblem.TRANSPORT, f"{where}: {result.outcome.value}{detail}")
    status = result.status_code
    if status is not None and not 200 <= status < 300:
        return (JsonProblem.HTTP_ERROR, f"{where}: HTTP {status}")
    if not result.has_body:
        return (JsonProblem.EMPTY_BODY, f"{where}: HTTP {status} with an empty body")
    return None


def _decode_body(
    text: str, *, field_map: JsonFieldMap, where: str
) -> _PageResult:
    """The JSON half of :func:`_decode_page`: no HTTP, only bytes."""
    if not text.strip():
        return _PageResult(problem=JsonProblem.EMPTY_BODY, error=f"{where}: empty body")

    try:
        payload = json.loads(text)
    except ValueError as exc:
        head = text.strip()[:60].replace("\n", " ")
        return _PageResult(
            problem=JsonProblem.NOT_JSON,
            error=(
                f"{where}: body is not JSON ({exc}); it starts {head!r} — the "
                "200-with-a-rendered-page case"
            ),
        )

    items = document_items(payload, field_map)
    if not items:
        if not field_map.keyed and isinstance(payload, list) and not payload:
            # An empty list is a real answer from a paginated endpoint asked for
            # a page past the end (`page=99` returns 200 with `[]`). The caller
            # decides whether that is the end of the walk or a drifted source.
            # A *keyed* shape never gets this benefit: a list there is a shape
            # change, not an empty page.
            return _PageResult(page=JsonPage(url="", items=()))
        shape_text = (
            f"a {type(payload).__name__} with keys "
            f"{', '.join(sorted(map(str, payload))[:12]) or '<none>'}"
            if isinstance(payload, dict)
            else f"a {type(payload).__name__} of {len(payload)} entry/entries"
            if isinstance(payload, list)
            else f"a {type(payload).__name__}"
        )
        wanted = (
            "a dict of item objects keyed by identifier"
            if field_map.keyed
            else f"a list of item objects at items={field_map.items!r}"
        )
        return _PageResult(
            problem=JsonProblem.NO_ITEMS,
            error=(
                f"{where}: valid JSON decoded to {shape_text} and this shape wants "
                f"{wanted}. The document is still being served and its shape "
                "changed, which is a different repair from an empty catalog — and "
                "an adapter that returned [] here would look exactly like a space "
                "with nothing on"
            ),
        )
    return _PageResult(page=JsonPage(url="", items=tuple(items)))


def _decode_page(
    result: FetchResult,
    *,
    shape: JsonShape,
    number: int,
    strict_content_type: bool = True,
) -> _PageResult:
    """Turn one :class:`FetchResult` into a :class:`JsonPage` or a problem.

    Never raises. Every judgement CLAUDE.md asks for happens here, in order:
    transport, status, content type, JSON, then *the shape of the JSON*.
    """
    where = f"{result.source_key} page {number} ({result.url})"

    transport = _transport_problem(result, where=where)
    if transport is not None:
        return _PageResult(problem=transport[0], error=transport[1])

    text = result.text
    tolerated: str | None = None
    if strict_content_type and result.content_type is not None:
        if not result.content_type_is(*JSON_CONTENT_TYPES):
            if result.content_type_is(*LENIENT_CONTENT_TYPES) and looks_like_json_document(
                text
            ):
                # Some hosts serve JSON as text/plain. Sloppy, not dishonest.
                tolerated = result.content_type
            else:
                head = text.strip()[:60].replace("\n", " ")
                html_note = (
                    " The body is an HTML document, not the feed we registered."
                    if looks_like_html(text)
                    else ""
                )
                return _PageResult(
                    problem=JsonProblem.WRONG_CONTENT_TYPE,
                    error=(
                        f"{where}: expected {' or '.join(JSON_CONTENT_TYPES)}, got "
                        f"{result.content_type} (HTTP {result.status_code}, "
                        f"{result.byte_count} bytes); it starts {head!r}.{html_note} "
                        "HTTP 200 is not success, and a body that is not the document "
                        "we registered must not be read as a catalog with nothing in it"
                    ),
                )

    decoded = _decode_body(text, field_map=shape.field_map, where=where)
    if decoded.page is None:
        return decoded
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
    return replace(
        decoded,
        page=replace(
            decoded.page,
            url=result.url,
            number=number,
            total=_header_int(result, shape.total_header),
            total_pages=_header_int(result, shape.total_pages_header),
            byte_count=result.byte_count,
        ),
        tolerated=tolerated,
        digest=digest,
    )


def _header_int(result: FetchResult, name: str | None) -> int | None:
    """One response header as an int. ``None`` when absent or unreadable."""
    if not name:
        return None
    return _int(result.headers.get(name.lower()))


# --------------------------------------------------------------------------- guards


def missing_required_params(
    url: str | httpx.URL, required: Iterable[tuple[str, str]]
) -> list[tuple[str, str, str | None]]:
    """``(name, wanted, actual)`` for every required parameter not on ``url``.

    Checked against the URL that was **actually requested**, not against
    ``sources.yaml``, because the point is to catch a request that went out
    wrong — including one assembled by a caller that never read the registry.
    """
    params = httpx.URL(str(url)).params
    missing: list[tuple[str, str, str | None]] = []
    for name, wanted in required:
        actual = params.get(name)
        if actual is None or str(actual).strip().lower() != wanted.strip().lower():
            missing.append((name, wanted, None if actual is None else str(actual)))
    return missing


def page_url(url: str | httpx.URL, number: int, *, param: str = "page") -> httpx.URL:
    """The same query string with ``page=N`` set.

    ``copy_merge_params`` rather than a rebuilt URL on purpose: ``orderby=date``
    and ``per_page=100`` must survive to page 4 unchanged, and reconstructing
    the query is another chance to drop the one parameter that matters.
    """
    return httpx.URL(str(url)).copy_merge_params({param: str(number)})


# --------------------------------------------------------------------------- parsing


def resolve_shape(name: str | JsonShape | None) -> JsonShape | None:
    """A shape name from the registry to the shape itself."""
    if isinstance(name, JsonShape):
        return name
    if not name:
        return None
    return SHAPES.get(name)


def _shape_name_for(ref: SourceRef | None, override: str | JsonShape | None) -> str | None:
    if isinstance(override, JsonShape):
        return override.name
    if override:
        return override
    if ref is None:
        return None
    return ref.source.shape


def parse_json(
    result: FetchResult,
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    today: dt.date | None = None,
    now: dt.datetime | None = None,
    strict_content_type: bool = True,
    shape: str | JsonShape | None = None,
    min_total: int | None = None,
    ref: SourceRef | None = None,
    fetcher: Fetcher | None = None,
    max_pages: int = MAX_PAGES,
) -> JsonParse:
    """Parse a fetched JSON document. Never raises.

    ``result`` is the document (page 1 for a paginated shape), already fetched
    by the caller. The shape comes from ``shape`` when given and otherwise from
    the registry entry (``ref.source.shape``); the amputation floor comes from
    ``min_total`` or ``ref.source.min_total``.

    Pages 2..n are fetched through ``fetcher`` when one is supplied, which is
    how the nightly run gets all 353 of The Crucible's products while staying
    inside one rate limiter, one ``robots.txt`` decision and one ``raw/``
    archive. Called **without** a fetcher — a test, or a human reading a file
    out of ``raw/`` — it parses the page it was given and sets
    :attr:`JsonParse.truncated` if more were on offer. That is a deliberate
    single-page call, not a silent loss.

    A 404, a rendered HTML page, a missing ``orderby=date``, a shrunken
    ``X-WP-Total``, a shape change and a failed page 3 all come back as a
    :class:`JsonParse` with ``ok=False`` and a distinct :class:`JsonProblem`.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    today = today or now.date()
    window_start = today
    window_end = today + dt.timedelta(days=horizon_days)
    where = f"{result.source_key} ({result.url})"
    name = _shape_name_for(ref, shape)
    if min_total is None and ref is not None:
        min_total = ref.source.min_total

    def fail(
        problem: JsonProblem,
        error: str,
        **extra: Any,
    ) -> JsonParse:
        return _failure(
            problem,
            error,
            space_id=result.space_id,
            label=result.label,
            source_url=result.url,
            shape=name or "",
            horizon_days=horizon_days,
            window_start=window_start,
            window_end=window_end,
            now=now,
            expected_min_total=min_total,
            **extra,
        )

    # --- which shape is this? -------------------------------------------------
    if not name:
        return fail(
            JsonProblem.NO_SHAPE,
            f"{where}: no shape was supplied. The json adapter maps a *bespoke* "
            "document onto the event schema, and the two registered documents "
            "(a dict keyed by 'YYYY-MM-DD_<amiliaId>' and a WooCommerce product "
            "list) have nothing in common — sniffing which one arrived would be "
            "a guess. Set shape on the source in sources.yaml; known shapes: "
            f"{', '.join(sorted(SHAPES))}",
        )
    known = resolve_shape(name)
    if known is None:
        return fail(
            JsonProblem.UNKNOWN_SHAPE,
            f"{where}: shape {name!r} is not implemented. Known shapes: "
            f"{', '.join(sorted(SHAPES))}",
        )

    # --- the transport --------------------------------------------------------
    transport = _transport_problem(result, where=where)
    if transport is not None:
        return fail(transport[0], transport[1])

    # --- the load-bearing query parameters, checked on the REQUEST ------------
    #
    # Before the body, deliberately. The Crucible without `orderby=date` answers
    # 200 with valid JSON and 98 of 353 products; there is nothing in that body
    # to find the mistake in, so the mistake is found on the way out instead.
    missing = missing_required_params(result.url, known.required_params)
    if missing:
        detail = "; ".join(
            f"{param}={wanted!r} (got {actual!r})" if actual is not None
            else f"{param}={wanted!r} (absent)"
            for param, wanted, actual in missing
        )
        return fail(
            JsonProblem.MISSING_REQUIRED_PARAM,
            f"{where}: the request is missing {detail}. This is not cosmetic and "
            "not a preference: without orderby=date this endpoint returns HTTP "
            "200, valid JSON and X-WP-Total: 98 instead of 353 — 72% of the "
            "catalog silently missing, covering only 40 of the 216 live courses, "
            "with nothing in the response that looks wrong. Restore the params "
            "block on this source in sources.yaml",
        )

    # --- page 1 ---------------------------------------------------------------
    first = _decode_page(
        result, shape=known, number=1, strict_content_type=strict_content_type
    )
    if first.page is None:
        return fail(first.problem, first.error or "the document could not be decoded")

    pages: list[JsonPage] = [first.page]
    notes: list[str] = []
    if first.tolerated:
        notes.append(f"content type {first.tolerated!r} tolerated: the body is real JSON")

    reported_total = first.page.total
    reported_pages = first.page.total_pages

    def build(
        tally: _Tally,
        *,
        problem: JsonProblem = JsonProblem.NONE,
        error: str | None = None,
        truncated: bool = False,
    ) -> JsonParse:
        return _build(
            tally,
            pages,
            shape=known.name,
            space_id=result.space_id,
            label=result.label,
            source_url=result.final_url or result.url,
            horizon_days=horizon_days,
            window_start=window_start,
            window_end=window_end,
            now=now,
            reported_total=reported_total,
            reported_pages=reported_pages,
            expected_min_total=min_total,
            document_digest=first.digest,
            document_bytes=first.page.byte_count,
            truncated=truncated,
            problem=problem,
            error=error,
        )

    # --- the amputation guard, checked on the RESPONSE -------------------------
    #
    # Independent of the parameter check above and deliberately not trusting it:
    # the parameter can be present and the server can change its mind, and a
    # catalog that loses 72% overnight must never be published as a small week.
    if min_total is not None and known.total_header:
        if reported_total is None:
            return fail(
                JsonProblem.NO_TOTAL,
                f"{where}: the registry expects at least {min_total} items and the "
                f"response carries no {known.total_header} header, so the guard "
                "against a silently truncated catalog cannot run. Headers present: "
                f"{', '.join(sorted(result.headers)) or '<none>'}. A source we can "
                "no longer check is not a source to publish on trust",
                reported_pages=reported_pages,
                pages=pages,
            )
        if reported_total < min_total:
            return fail(
                JsonProblem.TRUNCATED_CATALOG,
                f"{where}: {known.total_header} is {reported_total}, below the "
                f"{min_total} this source is registered to carry. The recorded "
                "cause is a missing orderby=date, which returns HTTP 200 and valid "
                "JSON with 98 of 353 products — 72% of the catalog gone, covering "
                "only 40 of the 216 live courses. Refusing to publish a catalog "
                "that may be quietly amputated; issue 0014 carries last night's "
                "forward instead",
                reported_total=reported_total,
                reported_pages=reported_pages,
                pages=pages,
            )

    # --- pagination, on the header and never until-error -----------------------
    #
    # `page=99` returns 200 with `[]`, not an error, so a walk that stopped on
    # failure would keep asking until MAX_PAGES and then call the result healthy.
    # The page count comes from the response.
    total_pages = reported_pages or 1
    truncated = False
    if known.paginated and total_pages > 1 and (fetcher is None or ref is None):
        # A deliberate single-page call: a test, or a human reading one file out
        # of raw/. Say so loudly in the result rather than returning 100 of 353
        # products as though that were the catalog.
        notes.append(
            f"{known.total_pages_header} is {total_pages} but no fetcher was "
            f"supplied, so only page 1 was read: {len(first.page)} of "
            f"{reported_total if reported_total is not None else '?'} items"
        )
        truncated = True
        total_pages = 1

    if known.paginated and total_pages > 1:
        if total_pages > max_pages:
            return fail(
                JsonProblem.RUNAWAY_PAGINATION,
                f"{where}: {known.total_pages_header} is {total_pages}, above the "
                f"{max_pages}-page ceiling. Refusing to walk a listing that claims "
                "to be an order of magnitude larger than the observed 4 pages; the "
                "counts collected so far cannot be trusted",
                reported_total=reported_total,
                reported_pages=reported_pages,
                pages=pages,
            )

        assert fetcher is not None and ref is not None  # narrowed above
        for number in range(2, total_pages + 1):
            url = page_url(result.url, number, param=known.page_param)
            page_result = fetcher.fetch_url(ref, url, label_suffix=f"p{number}")
            decoded = _decode_page(
                page_result,
                shape=known,
                number=number,
                strict_content_type=strict_content_type,
            )
            if decoded.page is None:
                # Page 1 was fine and a later page was not. Failing the whole
                # parse hands the night to carry-forward, which republishes
                # yesterday's complete set — strictly better than publishing a
                # partial one that looks healthy.
                collected = sum(len(page) for page in pages)
                return fail(
                    JsonProblem.PAGE_FAILED,
                    f"pagination stopped at page {number} of {total_pages}: "
                    f"{decoded.error} (problem={decoded.problem.value}; "
                    f"{collected} items collected before the failure, deliberately "
                    "not published as if they were the whole catalog)",
                    reported_total=reported_total,
                    reported_pages=reported_pages,
                    pages=pages,
                )
            pages.append(decoded.page)
            if not decoded.page.items:
                # A 200 with `[]` before the declared last page. Not an error —
                # it is what this endpoint answers past the end — but it *is*
                # a disagreement between the header and the body, and a reader
                # at 09:00 wants to know which one was wrong.
                notes.append(
                    f"page {number} of {total_pages} returned 200 with an empty "
                    "list; pagination stopped there rather than walking on. The "
                    f"{known.total_pages_header} header and the body disagree"
                )
                break

    # --- items -> events ------------------------------------------------------
    zone = _zone(known.zone)
    tally = _Tally()
    for page in pages:
        for key, item in page.items:
            _build_events(key, item, shape=known, zone=zone, page=page.number, tally=tally)

    # Both failures below go through ``build`` rather than ``fail`` so the tally
    # survives onto the result: "0 events, 1 sentinel dropped" and "0 events, 216
    # items with no dates" are different repairs, and the counts are the
    # difference.
    if not tally.items:
        return build(
            tally,
            problem=JsonProblem.NO_ITEMS,
            error=(
                f"{where}: the document decoded and carries no items at all. For a "
                "paginated listing this is a 200 with an empty body where a catalog "
                "belongs; it is never the same thing as a space with no classes"
            ),
        )

    if not tally.events:
        return build(
            tally,
            problem=JsonProblem.NO_DATES,
            error=(
                f"{where}: {tally.items} item(s) and not one usable start date "
                f"({tally.undated} with no {known.field_map.starts!r}, "
                f"{tally.bad_dates} unusable value(s), {tally.sentinels} sentinel(s) "
                f"matching {EPOCH_SENTINEL!r}). Expected "
                f"{known.field_map.start_format!r}; a catalog of this size with zero "
                "dates is a schema change, not a quiet week"
            ),
        )

    if tally.undated:
        notes.append(
            f"{tally.undated} of {tally.items} item(s) carry no usable "
            f"{known.field_map.starts!r} and were skipped"
            + (f" ({', '.join(tally.undated_ids[:5])})" if tally.undated_ids else "")
        )
    if tally.sentinels:
        notes.append(
            f"{tally.sentinels} start value(s) matched the literal sentinel "
            f"{EPOCH_SENTINEL!r} and were dropped before parsing — a naive "
            "two-digit-year parse maps it to 2070, which passes every horizon "
            "check without erroring"
        )
    if tally.bad_dates:
        notes.append(
            f"{tally.bad_dates} start value(s) were not a usable "
            f"{known.field_map.start_format!r} date (or fell outside "
            f"{MIN_YEAR}..{MAX_YEAR}) and were dropped"
        )
    if tally.missing_ids:
        notes.append(
            f"{tally.missing_ids} item(s) had no id — their UID falls back to "
            "sha1(space + start + title) in normalize.py"
        )
    if reported_total is not None and reported_total != tally.items:
        notes.append(
            f"{known.total_header} says {reported_total} and {tally.items} item(s) "
            "were decoded; not a gate, but worth a look"
        )

    return build(tally, error="; ".join(notes) or None, truncated=truncated)


def parse_json_text(
    document: str,
    *,
    shape: str | JsonShape,
    space_id: str = "",
    label: str = "",
    source_url: str = "",
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    today: dt.date | None = None,
    now: dt.datetime | None = None,
) -> JsonParse:
    """Parse **one JSON document**. The half with no HTTP in it.

    Split out for the same reason ``ics`` splits ``parse_ics_text``: the tests,
    and a human diffing two files out of ``raw/``, must be able to reach the
    parser without manufacturing a :class:`~pipeline.fetch.FetchResult`. It
    never paginates and it cannot check ``X-WP-Total`` — there are no headers
    here — so it is a parser, not a substitute for :func:`parse_json`.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    today = today or now.date()
    window_start = today
    window_end = today + dt.timedelta(days=horizon_days)
    known = resolve_shape(shape)
    where = f"{space_id}:{label}" if space_id or label else "<json>"

    if known is None:
        return _failure(
            JsonProblem.UNKNOWN_SHAPE,
            f"{where}: shape {shape!r} is not implemented. Known shapes: "
            f"{', '.join(sorted(SHAPES))}",
            space_id=space_id,
            label=label,
            source_url=source_url,
            horizon_days=horizon_days,
            window_start=window_start,
            window_end=window_end,
            now=now,
        )

    decoded = _decode_body(document, field_map=known.field_map, where=where)
    if decoded.page is None:
        return _failure(
            decoded.problem,
            decoded.error or "the document could not be decoded",
            space_id=space_id,
            label=label,
            source_url=source_url,
            shape=known.name,
            horizon_days=horizon_days,
            window_start=window_start,
            window_end=window_end,
            now=now,
        )

    page = replace(decoded.page, url=source_url, number=1)
    tally = _Tally()
    zone = _zone(known.zone)
    for key, item in page.items:
        _build_events(key, item, shape=known, zone=zone, page=1, tally=tally)

    return _build(
        tally,
        [page],
        shape=known.name,
        space_id=space_id,
        label=label,
        source_url=source_url,
        horizon_days=horizon_days,
        window_start=window_start,
        window_end=window_end,
        now=now,
        document_digest=decoded.digest
        or hashlib.sha1(document.encode("utf-8")).hexdigest()[:16],
        document_bytes=len(document.encode("utf-8")),
    )


# --------------------------------------------------------------------------- fetching


def fetch_json(
    fetcher: Fetcher,
    ref: SourceRef,
    *,
    horizon_days: int | None = None,
    today: dt.date | None = None,
    now: dt.datetime | None = None,
    conditional: bool = False,
    strict_content_type: bool = True,
    shape: str | JsonShape | None = None,
    min_total: int | None = None,
    max_pages: int = MAX_PAGES,
) -> JsonParse:
    """Fetch a registered source and parse it, following its pages. One call.

    The counterpart to :func:`pipeline.adapters.tribe_rest.fetch_tribe_rest`,
    and the entry point for the repair workflow: one source, every page, no run.

    The URL comes from :func:`pipeline.fetch.request_url`, which merges the
    registry's ``params`` block — and for The Crucible that block is
    ``orderby=date``, which is the difference between 353 products and 98.
    :func:`parse_json` re-checks it on the URL that actually went out rather
    than trusting this function to have done it.

    ``conditional`` defaults to **off**: a 304 on page 1 leaves no headers to
    read ``X-WP-TotalPages`` from. The nightly run reaches this adapter through
    :func:`pipeline.cli.process_source`, which applies the registry's own
    conditional-GET policy to page 1 and hands the result to
    :func:`parse_json` — and a 304 there is reported as
    :attr:`JsonProblem.NOT_MODIFIED`, which carry-forward answers.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    today = today or now.date()
    if horizon_days is None:
        horizon_days = fetcher.registry.horizon_days

    _space, source = ref
    result = fetcher.fetch_url(ref, request_url(source), conditional=conditional)
    return parse_json(
        result,
        horizon_days=horizon_days,
        today=today,
        now=now,
        strict_content_type=strict_content_type,
        shape=shape,
        min_total=min_total,
        ref=ref,
        fetcher=fetcher,
        max_pages=max_pages,
    )


#: The adapter entry point named by ``sources.yaml``'s ``adapter: json``.
parse = parse_json


__all__ = [
    "AMILIA_ACTIVITIES_CACHE",
    "EPOCH_SENTINEL",
    "MAX_PAGES",
    "MAX_YEAR",
    "MIN_YEAR",
    "SHAPES",
    "US_SHORT_FORMATS",
    "WOOCOMMERCE_STORE_PRODUCTS",
    "JsonEvent",
    "JsonFieldMap",
    "JsonPage",
    "JsonParse",
    "JsonProblem",
    "JsonShape",
    "document_items",
    "fetch_json",
    "iso_offset",
    "looks_like_json_document",
    "missing_required_params",
    "page_url",
    "parse",
    "parse_json",
    "parse_json_text",
    "parse_start",
    "parse_us_short",
    "resolve_shape",
    "start_values",
    "strip_tags",
    "woo_price_text",
    "woo_term_values",
]
