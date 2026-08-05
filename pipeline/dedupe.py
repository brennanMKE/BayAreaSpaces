"""Cross-source and cross-space collision handling.

The same event legitimately arrives twice. Ace publishes one calendar through
``tribe_rest``, ``ics`` and ``jsonld``; Maker Nexus has a Google Calendar and a
JSON cache; The Crucible has a catalog blob and a WooCommerce API that
cross-validate at 271 vs 274 events. Merging them into one entry without losing
the better copy is this module's whole job.

The rule, stated once
---------------------

**Within a space** — collision when ``space_id`` matches, the two starts are
within :data:`START_WINDOW`, and ``rapidfuzz.token_set_ratio`` of the two
normalized titles is at least :data:`WITHIN_SPACE_THRESHOLD` (85). The higher
``trust`` record wins and the loser's non-null fields fill the gaps.

**Across spaces** — the ``space_id`` match is dropped, but the bar rises to
:data:`CROSS_SPACE_THRESHOLD` (92) **and** the two events must agree on an
address. Both real cases in this registry are address-anchored: Sudo Room's
Luma feed carries an event at The Box Shop (951 Hudson Ave, San Francisco), and
a group posting to both Frontier Tower's Luma and its own page. Similarity
alone is not enough and must never be — "Open Shop Night" is the same three
words at six different buildings.

Why the 30-minute window is what makes the title comparison safe
----------------------------------------------------------------

**Never dedupe on title alone.** Maker Nexus has 76 distinct equipment-training
titles repeating across 171 instances — ``Laser Cutter (Equipment Training)``
appears again and again, and every appearance is a genuinely different session
somebody can genuinely book. A title-only rule collapses a working feed to a
handful of events and looks, from the outside, exactly like a healthy calendar.
The start window is the discriminator; the title is only the confirmation.

Trust, and what the winner keeps
--------------------------------

``trust`` comes from ``sources.yaml`` (:func:`trust_map` builds the lookup), and
a space's own site always outranks an aggregator or a partner's calendar. Ties
fall to an on-site record over an ``off_site`` one, then to whichever record
carries more populated fields, then to the UID so the outcome is deterministic
across runs.

**The winner keeps its own UID.** A merge that renamed the surviving event would
re-notify every subscriber for every merge, every night — which is the UID-churn
failure in ``CLAUDE.md`` arriving through the back door. :func:`merge_pair`
asserts it rather than trusting itself. ``first_seen`` takes the *earlier* of the
two for the same reason: RSS ``pubDate`` reads it (issue 0018), and a merge is
not a new event.

``content_hash`` **is** recomputed when a merge changes a field the hash covers.
The published content really did change, the recomputation is deterministic, and
issue 0014's store keys "did this event change?" on exactly that value.

Field-level preferences are policy, not special cases
-----------------------------------------------------

Research already established which source wins which field, and those findings
are data in :data:`DEFAULT_FIELD_PREFERENCES` rather than ``if space_id ==``
branches:

- **Maker Nexus** — the JSON cache is richer than the gCal for the events they
  share, so it wins ``price``. The gCal wins on *coverage* (3645 VEVENTs against
  30), which needs no rule: the events only it carries are never matched and
  simply survive. Capacity (``SpotsRemaining``) is the other documented
  preference and is **not expressible yet** — the canonical record has no
  capacity field. When one is added, add it to :data:`MERGEABLE_FIELDS` and to
  the preference below; declaring it here today would be validated and rejected,
  which is the intended loudness.
- **The Crucible** — the WooCommerce API wins ``description`` and ``price``; the
  catalog blob wins ``categories``, because the API's own categories are
  polluted with 60+ one-off per-product entries each equal to the class title.

A preference redirects **where a field is sourced from**; it never erases. If
the preferred source has nothing for that field, the winner keeps what it had —
and, critically, the non-preferred source is still not allowed to fill it. That
last clause is the whole point of The Crucible's categories rule: a blob entry
with no ``department`` must not quietly acquire a category named after itself.

Complexity
----------

This runs over a few thousand events, and 3645 of them are one space's. Both
passes sort by start and slide a window of :data:`START_WINDOW`, so the fuzzy
comparison only ever runs against events that are already time-plausible.
Candidate pairs become clusters, and each cluster is reduced against its own
winner — so a chain (A~B, B~C, A≁C) cannot drag an event 58 minutes away into a
merge just because something sat between them.

The merge log
-------------

Every merge is logged **and** returned as structured data
(:class:`DedupeResult.merges`), because 85 is a number somebody picked and the
only way to tune it is against real pairs. :class:`NearMiss` records the pairs
that were time-plausible and scored *just* under the bar, which is the other
half of that question. Issue 0017 reads :meth:`DedupeResult.summary` into
``health.json``.

What this does not replace
--------------------------

:func:`~pipeline.cli.collapse_uid_collisions` (issue 0012) stays underneath.
Two events can share a UID and be further apart than the window — a source
reusing one ``UID`` for two occurrences without an ``RRULE`` does exactly that —
and ``emit_ics`` refuses to publish a calendar containing two VEVENTs with one
UID. Dedupe is judgement; that is a floor, and a floor is still wanted.

Implemented by issue 0015.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

from rapidfuzz.fuzz import token_set_ratio

from pipeline.config import Registry
from pipeline.normalize import Event, content_hash, normalize_title

LOG = logging.getLogger("pipeline.dedupe")

# --------------------------------------------------------------------------- knobs

#: Two events further apart than this are never the same event, whatever their
#: titles say. This is what makes comparing 76 repeating Maker Nexus training
#: titles safe rather than catastrophic.
START_WINDOW = dt.timedelta(minutes=30)

#: ``token_set_ratio`` bar within one space. From the handoff.
WITHIN_SPACE_THRESHOLD = 85.0

#: The bar across spaces, where a false merge attributes one space's event to
#: another. Higher, *and* an address match is additionally required.
CROSS_SPACE_THRESHOLD = 92.0

#: Address agreement bar. ``token_set_ratio`` is intersection-based, so a bare
#: "951 Hudson Ave" scores 100 against "951 Hudson Ave, San Francisco, CA" —
#: which is the behavior wanted, since feeds truncate addresses differently.
ADDRESS_THRESHOLD = 90.0

#: An address is at minimum a number and a street. One token ("Oakland") is a
#: city, and a city is not an address match — without this floor, the
#: intersection scoring above would match every event in a city to every other.
MIN_ADDRESS_TOKENS = 2

#: Fields a merge may take from the loser. Deliberately the *published* content
#: and nothing else: UID, timestamps, provenance, quarantine state and
#: ``start_utc`` all belong to the winner by definition.
MERGEABLE_FIELDS: tuple[str, ...] = (
    "end_utc",
    "location_name",
    "address",
    "url",
    "price",
    "description",
    "categories",
    "summary_line",
    "rrule",
)

#: Fields :func:`~pipeline.normalize.content_hash` covers. Changing one of these
#: in a merge means the fingerprint has to be recomputed.
_HASHED_FIELDS = frozenset(
    {"end_utc", "location_name", "address", "url", "description"}
)

#: How far under a threshold a pair still gets recorded as a near miss.
NEAR_MISS_MARGIN = 10.0

#: Cap on retained near misses. They exist to be eyeballed; a run that produces
#: thousands has answered the tuning question already, and the count is kept
#: even when the detail is not.
MAX_NEAR_MISSES = 500


class DedupeError(Exception):
    """A dedupe policy is malformed, or a merge broke an invariant."""


# --------------------------------------------------------------------------- policy


@dataclass(frozen=True)
class FieldPreference:
    """One "this source wins this field" rule, keyed by space and source label.

    Research output as configuration. The alternative — a branch on
    ``space_id`` inside the merge — puts a finding about The Crucible's
    WooCommerce categories somewhere nobody looking at The Crucible will find
    it, and makes the next finding a code change.
    """

    space_id: str
    source_label: str
    fields: tuple[str, ...]
    #: Why. Ends up in nothing but the source file, and that is enough.
    note: str = ""

    def matches(self, event: Event) -> bool:
        return (
            event.space_id == self.space_id
            and event.source_label == self.source_label
        )


#: The preferences established by the 2026-08-05 source survey. See the module
#: docstring for the reasoning, and ``sources.yaml`` for the raw findings.
DEFAULT_FIELD_PREFERENCES: tuple[FieldPreference, ...] = (
    FieldPreference(
        space_id="maker-nexus",
        source_label="amilia-community-events-cache",
        fields=("price",),
        note=(
            "The JSON cache is richer than the gCal for the 30 events they "
            "share (Price, SpotsRemaining). The gCal still wins on coverage, "
            "which needs no rule. Capacity is not a field on Event yet."
        ),
    ),
    FieldPreference(
        space_id="the-crucible",
        source_label="woocommerce-store-api",
        fields=("description", "price"),
        note="353 products with descriptions, prices and stock; the blob has none.",
    ),
    FieldPreference(
        space_id="the-crucible",
        source_label="course-catalog-blob",
        fields=("categories",),
        note=(
            "The API's categories are polluted with 60+ one-off per-product "
            "entries equal to the class title. Filter on the blob's "
            "`department`, never on this — so the API must not supply the "
            "field even when the blob is empty."
        ),
    ),
)


@dataclass(frozen=True)
class DedupePolicy:
    """The knobs, in one object, so a tuning run is a value and not a patch."""

    within_space_threshold: float = WITHIN_SPACE_THRESHOLD
    cross_space_threshold: float = CROSS_SPACE_THRESHOLD
    start_window: dt.timedelta = START_WINDOW
    address_threshold: float = ADDRESS_THRESHOLD
    #: Cross-space merging can be turned off wholesale. The *address* half of
    #: the cross-space rule deliberately cannot: similarity alone across spaces
    #: is how one space's calendar ends up published under another's name.
    cross_space: bool = True
    fields: tuple[str, ...] = MERGEABLE_FIELDS
    field_preferences: tuple[FieldPreference, ...] = DEFAULT_FIELD_PREFERENCES

    def __post_init__(self) -> None:
        unknown = [name for name in self.fields if name not in MERGEABLE_FIELDS]
        if unknown:
            raise DedupeError(
                f"not mergeable fields: {unknown}. Known: {list(MERGEABLE_FIELDS)}"
            )
        for preference in self.field_preferences:
            missing = [
                name for name in preference.fields if name not in MERGEABLE_FIELDS
            ]
            if missing:
                raise DedupeError(
                    f"field preference {preference.space_id}:"
                    f"{preference.source_label} names {missing}, which "
                    f"Event does not carry as a mergeable field. Add it to "
                    "MERGEABLE_FIELDS (and to Event) rather than letting the "
                    "preference silently do nothing."
                )
        if self.start_window <= dt.timedelta(0):
            raise DedupeError("start_window must be positive")

    def owner_of(self, name: str, candidates: Sequence[Event]) -> Event | None:
        """The event whose source is *preferred* for field *name*, if any.

        ``None`` means no preference applies and the default rule (the winner
        keeps what it has; the loser fills a gap) governs. When both sides are
        preferred — two sources both claiming one field — the caller's ranking
        decides, and the first candidate wins because candidates arrive
        winner-first.
        """
        for candidate in candidates:
            for preference in self.field_preferences:
                if name in preference.fields and preference.matches(candidate):
                    return candidate
        return None

    def has_preference(self, name: str, candidates: Sequence[Event]) -> bool:
        return self.owner_of(name, candidates) is not None


DEFAULT_POLICY = DedupePolicy()


# --------------------------------------------------------------------------- trust

#: How a caller says what a source is worth. Either a ``{(space_id, label):
#: trust}`` mapping — which :func:`trust_map` builds from the registry — or a
#: callable, for the tests and for anything that wants to compute it.
TrustSource = Mapping[tuple[str, str], int] | Callable[[Event], int] | None


def trust_map(registry: Registry) -> dict[tuple[str, str], int]:
    """``{(space_id, source_label): trust}`` for every source in the registry.

    Keyed on the label the pipeline actually stamps onto an event —
    :func:`~pipeline.fetch.source_label`, i.e. the registry ``label`` or the
    adapter name — so the lookup cannot drift from what ``Event.source_label``
    holds.
    """
    from pipeline.fetch import source_label  # local: fetch imports config, not us

    return {
        (space.id, source_label(source)): source.trust
        for space in registry.spaces
        for source in space.sources
    }


def _trust_lookup(trust: TrustSource) -> Callable[[Event], int]:
    if trust is None:
        return lambda event: 0
    if callable(trust):
        return trust
    table = trust
    return lambda event: table.get((event.space_id, event.source_label), 0)


# --------------------------------------------------------------------------- matching


def similarity(left: str | None, right: str | None) -> float:
    """``token_set_ratio`` of the two **normalized** titles, 0-100.

    Normalizing first (:func:`~pipeline.normalize.normalize_title`: NFKC, case,
    punctuation, whitespace) means a curly apostrophe, a stray em dash and
    ``LASER FRYDAYS`` versus ``Laser Frydays`` are not three different reasons
    to miss a merge. An empty title scores 0 rather than matching everything.
    """
    a = normalize_title(left)
    b = normalize_title(right)
    if not a or not b:
        return 0.0
    return float(token_set_ratio(a, b))


def _numbers(tokens: Sequence[str]) -> set[str]:
    return {token for token in tokens if token.isdigit()}


#: Street-suffix and direction spellings folded to one form before comparing.
#: Feeds in this registry write the same building as ``951 Hudson Ave`` and
#: ``951 Hudson Avenue, San Francisco``, and ``token_set_ratio`` treats those as
#: two different tokens — which is a missed cross-space merge for no better
#: reason than an abbreviation. Deliberately short: postal normalization is a
#: rabbit hole, and the street number is doing the real work.
_ADDRESS_SYNONYMS: dict[str, str] = {
    "avenue": "ave",
    "street": "st",
    "road": "rd",
    "boulevard": "blvd",
    "drive": "dr",
    "court": "ct",
    "lane": "ln",
    "place": "pl",
    "square": "sq",
    "terrace": "ter",
    "parkway": "pkwy",
    "highway": "hwy",
    "suite": "ste",
    "north": "n",
    "south": "s",
    "east": "e",
    "west": "w",
    "california": "ca",
    "usa": "us",
}


def _address_tokens(text: str) -> list[str]:
    return [_ADDRESS_SYNONYMS.get(token, token) for token in text.split()]


def address_similarity(left: str | None, right: str | None) -> float:
    """0-100 agreement between two postal addresses. 0 when either is unusable.

    Not the same test as :func:`similarity`, because addresses fail differently:
    the same building is written ``951 Hudson Ave``, ``951 Hudson Avenue`` and
    ``951 Hudson Ave, San Francisco, CA 94124`` by three different feeds, while
    two genuinely different buildings on one street differ only in the number.
    So the street number is treated as decisive — when both sides carry one and
    they share none, the answer is 0 no matter how well the words match.
    """
    if not normalize_title(left) or not normalize_title(right):
        return 0.0
    tokens_a = _address_tokens(normalize_title(left))
    tokens_b = _address_tokens(normalize_title(right))
    if len(tokens_a) < MIN_ADDRESS_TOKENS or len(tokens_b) < MIN_ADDRESS_TOKENS:
        return 0.0
    a = " ".join(tokens_a)
    b = " ".join(tokens_b)
    if a == b:
        return 100.0
    # The house number, when both sides lead with one, is decisive: 951 and 953
    # Hudson Ave share a street, a city and a ZIP, and every one of those tokens
    # pushes the ratio up. Only the number says they are different buildings.
    head_a = tokens_a[0] if tokens_a[0].isdigit() else None
    head_b = tokens_b[0] if tokens_b[0].isdigit() else None
    if head_a is not None and head_b is not None and head_a != head_b:
        return 0.0
    numbers_a = _numbers(tokens_a)
    numbers_b = _numbers(tokens_b)
    if numbers_a and numbers_b and not (numbers_a & numbers_b):
        return 0.0
    return float(token_set_ratio(a, b))


def address_matches(
    left: str | None, right: str | None, *, threshold: float = ADDRESS_THRESHOLD
) -> bool:
    """True when two addresses name the same place. See :func:`address_similarity`."""
    return address_similarity(left, right) >= threshold


def start_delta(left: Event, right: Event) -> dt.timedelta:
    """Absolute distance between two starts. Both are UTC-aware by invariant."""
    return abs(left.start_utc - right.start_utc)


def within_window(left: Event, right: Event, window: dt.timedelta) -> bool:
    return start_delta(left, right) <= window


# --------------------------------------------------------------------------- records


class MergeKind(str, Enum):
    """Which of the two rules fired."""

    WITHIN_SPACE = "within_space"
    CROSS_SPACE = "cross_space"


def _present(value: Any) -> bool:
    """True when a field carries information. ``()`` and ``""`` do not."""
    if value is None:
        return False
    if isinstance(value, (str, tuple, list)):
        return len(value) > 0
    return True


def _richness(event: Event) -> int:
    """How many mergeable fields this record actually populates.

    The tie-break under ``trust``: when two sources are worth the same, the one
    carrying more of the event is the better base to merge into.
    """
    return sum(1 for name in MERGEABLE_FIELDS if _present(getattr(event, name)))


@dataclass(frozen=True)
class Merge:
    """One merge, in enough detail to argue about the threshold that caused it.

    Returned as data rather than only logged: issue 0017 counts these in
    ``health.json``, and tuning 85 means reading the scores that fired.
    """

    kind: MergeKind
    score: float
    #: Seconds between the two starts. Always within the policy window.
    start_delta_seconds: float
    #: Address agreement, ``None`` when the rule did not require it.
    address_score: float | None

    winner_uid: str
    winner_space_id: str
    winner_source_label: str
    winner_trust: int
    winner_title: str
    winner_start: str

    loser_uid: str
    loser_space_id: str
    loser_source_label: str
    loser_trust: int
    loser_title: str
    loser_start: str

    #: Fields the loser filled because the winner had none.
    fields_filled: tuple[str, ...] = ()
    #: Fields resolved by a :class:`FieldPreference` rather than by trust.
    fields_preferred: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready. The wire form ``health.json`` (issue 0017) carries."""
        return {
            "kind": self.kind.value,
            "score": round(self.score, 2),
            "start_delta_seconds": round(self.start_delta_seconds, 1),
            "address_score": (
                round(self.address_score, 2) if self.address_score is not None else None
            ),
            "winner": {
                "uid": self.winner_uid,
                "space_id": self.winner_space_id,
                "source_label": self.winner_source_label,
                "trust": self.winner_trust,
                "title": self.winner_title,
                "start_utc": self.winner_start,
            },
            "loser": {
                "uid": self.loser_uid,
                "space_id": self.loser_space_id,
                "source_label": self.loser_source_label,
                "trust": self.loser_trust,
                "title": self.loser_title,
                "start_utc": self.loser_start,
            },
            "fields_filled": list(self.fields_filled),
            "fields_preferred": list(self.fields_preferred),
        }

    def line(self) -> str:
        """One log line. Both UIDs and the score, which is what tuning needs."""
        return (
            f"[{self.kind.value}] {self.winner_uid} <- {self.loser_uid} "
            f"score={self.score:.1f} delta={self.start_delta_seconds / 60:.0f}min"
            + (
                f" address={self.address_score:.1f}"
                if self.address_score is not None
                else ""
            )
            + (f" filled={list(self.fields_filled)}" if self.fields_filled else "")
            + (
                f" preferred={list(self.fields_preferred)}"
                if self.fields_preferred
                else ""
            )
            + f" ({self.winner_title!r} <- {self.loser_title!r})"
        )

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        return self.line()


@dataclass(frozen=True)
class NearMiss:
    """A time-plausible pair that scored just under the bar.

    The other half of "log every merge so the threshold can be tuned": the
    merges say what 85 caught, and these say what it let through. A cluster of
    near misses at 83 on one space is the argument for moving the number, and
    without them nobody would ever see it.
    """

    kind: MergeKind
    score: float
    threshold: float
    start_delta_seconds: float
    left_uid: str
    right_uid: str
    left_title: str
    right_title: str
    #: ``"score"`` or ``"address"`` — which half of the rule it failed.
    failed: str = "score"

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "score": round(self.score, 2),
            "threshold": self.threshold,
            "failed": self.failed,
            "start_delta_seconds": round(self.start_delta_seconds, 1),
            "left_uid": self.left_uid,
            "right_uid": self.right_uid,
            "left_title": self.left_title,
            "right_title": self.right_title,
        }


@dataclass(frozen=True)
class DedupeResult:
    """Survivors and the accounting for everything that did not survive.

    Iterating or ``len()``-ing yields the **surviving** events, matching
    :class:`~pipeline.normalize.Normalization` and
    :class:`~pipeline.filters.FilterResult`.
    """

    events: tuple[Event, ...] = ()
    merges: tuple[Merge, ...] = ()
    near_misses: tuple[NearMiss, ...] = ()
    #: Total near misses observed, including any past :data:`MAX_NEAR_MISSES`.
    near_miss_count: int = 0
    input_count: int = 0
    #: Fuzzy comparisons actually performed. The number that says whether the
    #: blocking is doing its job — an accidental quadratic shows up here first.
    comparisons: int = 0
    policy: DedupePolicy = field(default_factory=lambda: DEFAULT_POLICY)

    @property
    def event_count(self) -> int:
        return len(self.events)

    @property
    def merge_count(self) -> int:
        return len(self.merges)

    @property
    def merges_by_kind(self) -> dict[str, int]:
        counts = {kind.value: 0 for kind in MergeKind}
        for merge in self.merges:
            counts[merge.kind.value] += 1
        return counts

    @property
    def merges_by_space(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for merge in self.merges:
            counts[merge.winner_space_id] = counts.get(merge.winner_space_id, 0) + 1
        return counts

    def merges_for(self, uid: str) -> tuple[Merge, ...]:
        """Every merge that touched *uid*, as winner or loser."""
        return tuple(
            merge
            for merge in self.merges
            if uid in (merge.winner_uid, merge.loser_uid)
        )

    def summary(self) -> dict[str, Any]:
        """JSON-ready counts and the merge log. Issue 0017 writes this out."""
        return {
            "input": self.input_count,
            "kept": self.event_count,
            "merged": self.merge_count,
            "by_kind": self.merges_by_kind,
            "by_space": self.merges_by_space,
            "comparisons": self.comparisons,
            "thresholds": {
                "within_space": self.policy.within_space_threshold,
                "cross_space": self.policy.cross_space_threshold,
                "address": self.policy.address_threshold,
                "start_window_minutes": self.policy.start_window.total_seconds() / 60,
            },
            "merges": [merge.as_dict() for merge in self.merges],
            "near_miss_count": self.near_miss_count,
            "near_misses": [miss.as_dict() for miss in self.near_misses],
        }

    def __iter__(self) -> Iterator[Event]:
        return iter(self.events)

    def __len__(self) -> int:
        return len(self.events)

    def __getitem__(self, index: int) -> Event:
        return self.events[index]

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        return (
            f"dedupe kept {self.event_count}/{self.input_count}, "
            f"{self.merge_count} merges ({self.merges_by_kind}), "
            f"{self.comparisons} comparisons"
        )


# --------------------------------------------------------------------------- merging


def merge_pair(
    winner: Event,
    loser: Event,
    *,
    policy: DedupePolicy = DEFAULT_POLICY,
) -> tuple[Event, tuple[str, ...], tuple[str, ...]]:
    """Fold *loser* into *winner*. Returns ``(event, filled, preferred)``.

    Two rules, in this order:

    1. **A field with a preference is sourced only from the preferred source.**
       If that source has nothing, the winner keeps what it had — the other
       source is still not allowed to supply it. See The Crucible's categories.
    2. Otherwise the loser fills a gap the winner has, and never overwrites.

    ``uid`` is not in :data:`MERGEABLE_FIELDS` and is asserted unchanged on the
    way out: a merge that renamed the surviving event would re-notify every
    subscriber, every night, for every merge.
    """
    candidates = (winner, loser)
    changes: dict[str, Any] = {}
    filled: list[str] = []
    preferred: list[str] = []

    for name in policy.fields:
        winner_value = getattr(winner, name)
        owner = policy.owner_of(name, candidates)
        if owner is not None:
            owner_value = getattr(owner, name)
            if _present(owner_value) and owner_value != winner_value:
                changes[name] = owner_value
                preferred.append(name)
            continue
        loser_value = getattr(loser, name)
        if not _present(winner_value) and _present(loser_value):
            changes[name] = loser_value
            filled.append(name)

    # A merge is not a new event. RSS pubDate reads first_seen (issue 0018), so
    # the earlier sighting is the true one; last_seen takes the later.
    if loser.first_seen is not None and (
        winner.first_seen is None or loser.first_seen < winner.first_seen
    ):
        changes["first_seen"] = loser.first_seen
    if loser.last_seen is not None and (
        winner.last_seen is None or loser.last_seen > winner.last_seen
    ):
        changes["last_seen"] = loser.last_seen

    if not changes:
        return winner, (), ()

    merged = replace(winner, **changes)

    if _HASHED_FIELDS & changes.keys():
        merged = replace(
            merged,
            content_hash=content_hash(
                title=merged.title,
                start_utc=merged.start_utc,
                end_utc=merged.end_utc,
                location_name=merged.location_name,
                address=merged.address,
                url=merged.url,
                description=merged.description,
            ),
        )

    if merged.uid != winner.uid:  # pragma: no cover - structurally impossible
        raise DedupeError(
            f"merge changed the surviving UID ({winner.uid} -> {merged.uid}). "
            "Every merge would re-notify every subscriber; see CLAUDE.md."
        )
    return merged, tuple(filled), tuple(preferred)


# --------------------------------------------------------------------------- engine


@dataclass
class _Pass:
    """One pass's working state. Mutable on purpose; nothing escapes it."""

    kind: MergeKind
    threshold: float
    policy: DedupePolicy
    trust: Callable[[Event], int]
    require_address: bool

    merges: list[Merge] = field(default_factory=list)
    near_misses: list[NearMiss] = field(default_factory=list)
    near_miss_count: int = 0
    comparisons: int = 0
    #: False while a cluster is being reduced. The same pair is tested twice —
    #: once to find the cluster, once against the winner it would fold into —
    #: and counting or reporting it twice would make the tuning data lie.
    scanning: bool = True

    # -- pair test ------------------------------------------------------------

    def pair(self, left: Event, right: Event) -> tuple[float, float | None] | None:
        """``(score, address_score)`` when these two are the same event.

        ``None`` otherwise, with a near miss recorded when it was close.
        """
        if self.kind is MergeKind.WITHIN_SPACE:
            if left.space_id != right.space_id:
                return None
        elif left.space_id == right.space_id:
            return None

        delta = start_delta(left, right)
        if delta > self.policy.start_window:
            return None

        if self.scanning:
            self.comparisons += 1
        score = similarity(left.title, right.title)
        if score < self.threshold:
            if score >= self.threshold - NEAR_MISS_MARGIN:
                self._near_miss(left, right, score, delta, "score")
            return None

        if not self.require_address:
            return score, None

        address_score = address_similarity(left.address, right.address)
        if address_score < self.policy.address_threshold:
            # The half of the cross-space rule that stops "Open Shop Night" at
            # six different buildings from becoming one event.
            self._near_miss(left, right, score, delta, "address")
            return None
        return score, address_score

    def _near_miss(
        self,
        left: Event,
        right: Event,
        score: float,
        delta: dt.timedelta,
        failed: str,
    ) -> None:
        if not self.scanning:
            return
        self.near_miss_count += 1
        if len(self.near_misses) >= MAX_NEAR_MISSES:
            return
        self.near_misses.append(
            NearMiss(
                kind=self.kind,
                score=score,
                threshold=self.threshold,
                start_delta_seconds=delta.total_seconds(),
                left_uid=left.uid,
                right_uid=right.uid,
                left_title=left.title,
                right_title=right.title,
                failed=failed,
            )
        )

    # -- ranking --------------------------------------------------------------

    def rank(self, event: Event) -> tuple[int, int, int, str]:
        """Sort key, best first. Trust, then on-site, then richness, then UID.

        ``off_site`` breaks a trust tie toward the record whose own space owns
        the building: when Sudo Room's Luma and The Box Shop's own calendar
        carry the same Hudson Ave event, the space that is actually there is the
        better home for it. The UID is last so the result is identical on every
        run — determinism here is what keeps merges from churning subscribers.
        """
        return (-self.trust(event), int(event.off_site), -_richness(event), event.uid)

    # -- clustering -----------------------------------------------------------

    def run(self, events: Sequence[Event]) -> list[Event]:
        """Sliding window over start time, then reduce each cluster.

        Sorting by start and stopping the backward scan at the first event more
        than one window away is what keeps this linear-ish over the 3645 events
        Maker Nexus alone contributes. Only pairs that survive the window ever
        reach ``token_set_ratio``.
        """
        order = sorted(range(len(events)), key=lambda i: (events[i].start_utc, events[i].uid))
        parent = list(range(len(events)))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(i: int, j: int) -> None:
            a, b = find(i), find(j)
            if a != b:
                parent[b] = a

        for position, index in enumerate(order):
            event = events[index]
            for back in range(position - 1, -1, -1):
                other_index = order[back]
                other = events[other_index]
                if event.start_utc - other.start_utc > self.policy.start_window:
                    break
                if self.pair(event, other) is not None:
                    union(other_index, index)

        clusters: dict[int, list[int]] = {}
        for index in range(len(events)):
            clusters.setdefault(find(index), []).append(index)

        survivors: list[tuple[int, Event]] = []
        for members in clusters.values():
            if len(members) == 1:
                index = members[0]
                survivors.append((index, events[index]))
                continue
            self.scanning = False
            try:
                survivors.extend(self._reduce(members, events))
            finally:
                self.scanning = True

        # Restore the caller's order, keyed on where each survivor came in.
        survivors.sort(key=lambda pair: pair[0])
        return [event for _, event in survivors]

    def _reduce(
        self, members: Sequence[int], events: Sequence[Event]
    ) -> list[tuple[int, Event]]:
        """Collapse one cluster, re-testing every loser against the winner.

        Clustering is transitive and the pair test is not: A at 10:00 and C at
        10:58 can end up in one cluster because B sat at 10:29 between them.
        Absorbing C into A on that basis would merge two events an hour apart,
        so each member has to match the winner it is being folded into. What
        does not match goes back through the same process and may form its own
        group — which is the honest answer for a genuine chain.
        """
        remaining = sorted(members, key=lambda index: self.rank(events[index]))
        result: list[tuple[int, Event]] = []

        while remaining:
            winner_index = remaining.pop(0)
            base = events[winner_index]
            winner = base
            # Every verdict is taken against the *unmerged* winner. Merging can
            # fill in an address, and a record must not become mergeable
            # because something it already absorbed supplied the field the rule
            # tests on.
            losers: list[tuple[int, float, float | None]] = []
            leftover: list[int] = []
            for index in remaining:
                verdict = self.pair(base, events[index])
                if verdict is None:
                    leftover.append(index)
                else:
                    losers.append((index, verdict[0], verdict[1]))
            remaining = leftover

            for loser_index, score, address_score in losers:
                loser = events[loser_index]
                winner, filled, preferred = merge_pair(
                    winner, loser, policy=self.policy
                )
                merge = Merge(
                    kind=self.kind,
                    score=score,
                    start_delta_seconds=start_delta(winner, loser).total_seconds(),
                    address_score=address_score,
                    winner_uid=winner.uid,
                    winner_space_id=winner.space_id,
                    winner_source_label=winner.source_label,
                    winner_trust=self.trust(winner),
                    winner_title=winner.title,
                    winner_start=winner.start_utc.isoformat(),
                    loser_uid=loser.uid,
                    loser_space_id=loser.space_id,
                    loser_source_label=loser.source_label,
                    loser_trust=self.trust(loser),
                    loser_title=loser.title,
                    loser_start=loser.start_utc.isoformat(),
                    fields_filled=filled,
                    fields_preferred=preferred,
                )
                self.merges.append(merge)
                # Every merge, logged. 85 is a number somebody picked, and this
                # is the only record of what it did to real data.
                LOG.info("merge %s", merge.line())

            result.append((winner_index, winner))

        return result


def dedupe(
    events: Iterable[Event],
    *,
    trust: TrustSource = None,
    policy: DedupePolicy | None = None,
) -> DedupeResult:
    """Collapse duplicate events. The entry point the nightly run calls.

    Runs after every source has been normalized and filtered, because a
    collision is by definition something no single source can see. Two passes,
    in this order:

    1. **Within a space**, at :attr:`DedupePolicy.within_space_threshold`. The
       common case, and the cheap one — Ace's three feeds, Maker Nexus's two,
       The Crucible's two.
    2. **Across spaces**, at the higher
       :attr:`DedupePolicy.cross_space_threshold` *and* an address match, over
       the survivors of the first pass. Doing it second means a space's
       internal duplicates are already one record, so a cross-space merge
       compares the best copy each space has rather than an arbitrary one.

    *trust* is a ``{(space_id, source_label): trust}`` mapping (see
    :func:`trust_map`) or a callable. Omitting it makes every source equally
    trusted, which is a legitimate thing to want in a test and a bad thing to
    ship: with no trust, the merge winner falls to field count and UID order.

    Quarantined events are not passed here — they never reach this stage.
    """
    policy = policy or DEFAULT_POLICY
    items = list(events)
    lookup = _trust_lookup(trust)

    within = _Pass(
        kind=MergeKind.WITHIN_SPACE,
        threshold=policy.within_space_threshold,
        policy=policy,
        trust=lookup,
        require_address=False,
    )
    survivors = within.run(items)

    merges = list(within.merges)
    near_misses = list(within.near_misses)
    near_miss_count = within.near_miss_count
    comparisons = within.comparisons

    if policy.cross_space:
        across = _Pass(
            kind=MergeKind.CROSS_SPACE,
            threshold=policy.cross_space_threshold,
            policy=policy,
            trust=lookup,
            require_address=True,
        )
        survivors = across.run(survivors)
        merges.extend(across.merges)
        near_misses.extend(across.near_misses[: max(0, MAX_NEAR_MISSES - len(near_misses))])
        near_miss_count += across.near_miss_count
        comparisons += across.comparisons

    result = DedupeResult(
        events=tuple(survivors),
        merges=tuple(merges),
        near_misses=tuple(near_misses),
        near_miss_count=near_miss_count,
        input_count=len(items),
        comparisons=comparisons,
        policy=policy,
    )
    LOG.info(
        "dedupe kept %d of %d events; %d merges (%s), %d near misses, "
        "%d fuzzy comparisons",
        result.event_count,
        result.input_count,
        result.merge_count,
        result.merges_by_kind,
        result.near_miss_count,
        result.comparisons,
    )
    return result


__all__ = [
    "ADDRESS_THRESHOLD",
    "CROSS_SPACE_THRESHOLD",
    "DEFAULT_FIELD_PREFERENCES",
    "DEFAULT_POLICY",
    "MAX_NEAR_MISSES",
    "MERGEABLE_FIELDS",
    "MIN_ADDRESS_TOKENS",
    "NEAR_MISS_MARGIN",
    "START_WINDOW",
    "WITHIN_SPACE_THRESHOLD",
    "DedupeError",
    "DedupePolicy",
    "DedupeResult",
    "FieldPreference",
    "Merge",
    "MergeKind",
    "NearMiss",
    "TrustSource",
    "address_matches",
    "address_similarity",
    "dedupe",
    "merge_pair",
    "similarity",
    "start_delta",
    "trust_map",
    "within_window",
]
