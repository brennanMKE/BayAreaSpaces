"""Registry filters, applied after normalize and before dedupe.

The stage that turns a source's whole calendar into the part of it this project
publishes: keep the events at the space's own building, drop the equipment
checkouts, drop the cross-posted third-party events.

Why this is its own module and its own issue
--------------------------------------------

**Filters drop events silently.** That puts them in the same hazard class as a
wrong feed URL: nothing errors, nothing is empty enough to notice, and a space
just quietly has fewer events than it should. The measured example is in
``spaces/frontier-makerspace.md`` — the originally *guessed* Frontier filter
(``Makerspace`` / ``Arts and Music`` / ``Robotics``) kept **9 of 262 events**,
because ``Arts and Music`` never appears in that feed; it is ``Arts & Music``.
A green run, a published calendar, and 97% of a building missing.

So everything here is built around making a drop visible:

- Every dropped event is logged with the rule *and the pattern* that dropped
  it, at ``DEBUG``, tagged ``filter drop`` so one grep answers "what happened
  to that event?".
- Every result carries :attr:`FilterResult.drops_by_rule` and
  :attr:`FilterResult.hits_by_pattern`, with an entry for every **configured**
  rule and pattern even when it is zero — a pattern that never matches is the
  ``Arts and Music`` bug, and it only shows up as a zero.
- A filter set that removes almost everything logs at ``WARNING`` on its own,
  without waiting for a human to compare counts.

Matching, stated explicitly
---------------------------

**Case-insensitive substring, everywhere.** ``location_contains: ["995
Market"]`` matches ``… 995 MARKET ST …``; ``title_excludes: ["(Equipment
Training)"]`` drops ``Laser Cutter (Equipment Training)``. No globbing, no
regex, no word boundaries — patterns in ``sources.yaml`` are verbatim
fragments observed in the real feed, and anything cleverer would be a second
thing to get wrong.

**HTML entities are matched in both directions.** The Crucible's blob stores
``Glass Fusing &amp; Slumping`` and never ``Glass Fusing & Slumping``. Rather
than making every registry author remember which feed escapes what, each value
*and* each pattern is compared both as written and HTML-unescaped, so
``"Glass Fusing & Slumping"``, ``"Glass Fusing &amp; Slumping"`` and the two
mixed forms all match the same events. Unescaping only for comparison keeps
:class:`~pipeline.normalize.Event` verbatim, which matters because the escaped
form is what the source actually said.

What "missing location" means here
----------------------------------

The invariant in ``CLAUDE.md``: *any* ``location_contains`` filter must decide
what happens to an event whose ``LOCATION`` is not an address, and the default
is to **keep** it. 40 of Frontier Tower's 262 events (15%) set ``LOCATION`` to
a bare ``https://luma.com/event/evt-…`` URL when the host hides the venue
behind registration, and several of those are the makerspace events this
project most wants — ``LASER FRYDAYS: Laser Cutter Training``, ``Frontier
Makerspace All-Hands & Demo Night``, ``The SF Bay Area LeRobot Hackathon``.

Issue 0009 already resolves those to a real address through
``space.address_override``, so at this stage there are three different states
that all look like "no location" from a distance. This module matches on
:attr:`~pipeline.normalize.Event.location_name` — the source's own ``LOCATION``
verbatim — and calls it :attr:`LocationState.PRESENT`, :attr:`EMPTY` or
:attr:`URL_ONLY`:

============================  ===================  ============================
``location_name``             ``address_source``   state
============================  ===================  ============================
a venue or street address     ``source``           ``present``
a venue or street address     ``override``         ``present`` (still matched)
``None`` / blank              ``none``             ``empty``
``None`` / blank              ``override``         ``empty``
``https://luma.com/event/…``  ``none``/``override``  ``url_only``
============================  ===================  ============================

**A resolved address is deliberately not matched.** When
:attr:`~pipeline.normalize.Event.address` differs from ``location_name`` it is
the registry's own ``address_override``, and filtering our own override against
our own filter is circular: for Humanmade or Hacker Dojo, whose feeds carry no
``LOCATION`` at all, a ``location_contains: ["Humanmade"]`` would match the
override on every single event and the filter would silently become a no-op
that looks like it is working. The registry filters on what the *source* said;
that is the only thing that carries information.

Both ``empty`` and ``url_only`` are "missing" for
``location_allow_when_missing``, and the two are distinguished in the log line
and in the drop record so that "these vanished because Luma hid the address"
and "these vanished because the feed has no venue field" never get confused.
Events kept this way are counted in
:attr:`FilterResult.kept_on_missing_location`, so a source whose filter is
being carried entirely by the fallback is visible rather than merely lucky.

Order of judgement
------------------

Excludes first (title, location, categories), then the keep-lists (title,
location). The first rule that fires owns the drop, so
:attr:`FilterResult.drops_by_rule` sums to exactly the number of dropped
events. Excludes come first because an explicit exclusion is a more specific
statement about an event than a failure to appear in a keep-list, and it is the
more useful thing to read in a log line.

Quarantined events (issue 0009) are not filtered. They are already withheld
from publication and exist to be looked at; running them through a filter would
only make a broken extraction harder to find.

Implemented by issue 0010.
"""

from __future__ import annotations

import html
import logging
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pipeline.config import Filters
from pipeline.normalize import Event, Normalization, is_bare_url

LOG = logging.getLogger("pipeline.filters")

#: A filter set that removes at least this share of a feed is reported at
#: ``WARNING`` rather than ``INFO``. The Frontier guess kept 3% and nobody
#: noticed for a week; this is the tripwire for the next one of those.
LOUD_DROP_RATE = 0.9

#: …but only once the feed is big enough for a rate to mean anything. Three of
#: four events dropped from a tiny calendar is normal.
LOUD_DROP_MIN_EVENTS = 5


# --------------------------------------------------------------------------- verdicts


class FilterRule(str, Enum):
    """Which registry key dropped an event.

    The values are the ``sources.yaml`` key names verbatim, so a log line or a
    ``health.json`` entry names something the operator can actually go and edit.
    :attr:`LOCATION_MISSING` is the one addition: it is ``location_contains``
    combined with ``location_allow_when_missing: false``, split out because
    "dropped for having no address" and "dropped for having the wrong address"
    are different problems with different fixes.
    """

    TITLE_EXCLUDES = "title_excludes"
    LOCATION_EXCLUDES = "location_excludes"
    CATEGORIES_EXCLUDE = "categories_exclude"
    TITLE_CONTAINS = "title_contains"
    LOCATION_CONTAINS = "location_contains"
    LOCATION_MISSING = "location_missing"


class LocationState(str, Enum):
    """What the source's own ``LOCATION`` amounts to. See the module docstring."""

    PRESENT = "present"
    EMPTY = "empty"
    URL_ONLY = "url_only"

    @property
    def is_missing(self) -> bool:
        """True for the two states ``location_allow_when_missing`` governs."""
        return self is not LocationState.PRESENT


@dataclass(frozen=True)
class Filtered:
    """One event a filter removed, with enough detail to argue about it.

    Parallel to :class:`~pipeline.normalize.Dropped`: nothing leaves this stage
    without landing somewhere it can be counted and read back.
    """

    rule: FilterRule
    #: The verbatim registry pattern that fired, or ``None`` for a keep-list
    #: rule (where the drop is caused by *no* pattern matching).
    pattern: str | None
    space_id: str
    source_label: str
    uid: str
    title: str
    #: The source's ``LOCATION`` verbatim, as matched — not the resolved address.
    location: str | None
    detail: str = ""

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        return f"{self.rule.value}: {self.title!r} ({self.detail})"


@dataclass(frozen=True)
class FilterResult:
    """Survivors, casualties, and a per-rule accounting of the difference.

    Iterating or ``len()``-ing yields the **kept** events, matching
    :class:`~pipeline.normalize.Normalization`. Issue 0017 consumes
    :meth:`summary` for ``health.json``.
    """

    events: tuple[Event, ...] = ()
    dropped: tuple[Filtered, ...] = ()

    space_id: str = ""
    source_label: str = ""

    #: Every **configured** rule, including ones that dropped nothing. A zero
    #: here against a non-empty feed is how a mistyped pattern announces itself.
    drops_by_rule: dict[str, int] = field(default_factory=dict)
    #: ``{rule: {pattern: hits}}`` for every configured pattern — how many
    #: events that pattern matched, whether or not it was the pattern the drop
    #: was attributed to. Every matching pattern gets credit (Frontier's
    #: ``Berlinhouse`` overlaps ``Frontier Tower`` on most events), so these do
    #: **not** sum to a drop count; :attr:`drops_by_rule` is the accounting and
    #: this is the diagnostic. Zero means the string never appears in the feed.
    hits_by_pattern: dict[str, dict[str, int]] = field(default_factory=dict)
    #: Events that survived ``location_contains`` only because their location
    #: was missing and ``location_allow_when_missing`` is on. 40 of Frontier
    #: Tower's 262, and the reason this issue exists.
    kept_on_missing_location: int = 0

    @property
    def kept_count(self) -> int:
        return len(self.events)

    @property
    def dropped_count(self) -> int:
        return len(self.dropped)

    @property
    def input_count(self) -> int:
        return len(self.events) + len(self.dropped)

    @property
    def drop_rate(self) -> float:
        """Share of the input removed. ``0.0`` for an empty input."""
        return self.dropped_count / self.input_count if self.input_count else 0.0

    @property
    def dead_patterns(self) -> tuple[tuple[str, str], ...]:
        """``(rule, pattern)`` pairs that matched nothing at all.

        The ``Arts and Music`` detector. A dead pattern is not proof of a typo —
        a legitimate exclude often has nothing to exclude tonight — but on a
        keep-list it is the first thing to check when a space goes quiet.
        """
        return tuple(
            (rule, pattern)
            for rule, patterns in self.hits_by_pattern.items()
            for pattern, hits in patterns.items()
            if hits == 0
        )

    def dropped_for(self, rule: FilterRule) -> tuple[Filtered, ...]:
        return tuple(item for item in self.dropped if item.rule is rule)

    def summary(self) -> dict[str, Any]:
        """JSON-ready counts for ``health.json`` (issue 0017)."""
        return {
            "space_id": self.space_id,
            "source_label": self.source_label,
            "input": self.input_count,
            "kept": self.kept_count,
            "dropped": self.dropped_count,
            "drop_rate": round(self.drop_rate, 4),
            "by_rule": dict(self.drops_by_rule),
            "by_pattern": {
                rule: dict(patterns) for rule, patterns in self.hits_by_pattern.items()
            },
            "kept_on_missing_location": self.kept_on_missing_location,
            "dead_patterns": [
                {"rule": rule, "pattern": pattern}
                for rule, pattern in self.dead_patterns
            ],
        }

    def __iter__(self) -> Iterator[Event]:
        return iter(self.events)

    def __len__(self) -> int:
        return len(self.events)

    def __getitem__(self, index: int) -> Event:
        return self.events[index]

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        return (
            f"{self.space_id}:{self.source_label} kept {self.kept_count}/"
            f"{self.input_count}, dropped {self.dropped_count}"
        )


# --------------------------------------------------------------------------- matching


def _forms(text: str) -> tuple[str, ...]:
    """The casefolded forms a value is compared in: as written, and unescaped."""
    folded = text.casefold()
    unescaped = html.unescape(folded)
    return (folded,) if unescaped == folded else (folded, unescaped)


def matches(text: str | None, pattern: str) -> bool:
    """Case-insensitive substring test, entity-insensitive in both directions.

    ``matches("Glass Fusing &amp; Slumping", "Glass Fusing & Slumping")`` is
    ``True``, and so is the reverse. An empty pattern never matches — an empty
    string is a substring of everything, and a filter that keeps or drops the
    entire feed by accident is exactly what this module exists to prevent.
    """
    if not text or not pattern:
        return False
    haystacks = _forms(text)
    needles = _forms(pattern)
    return any(needle in haystack for haystack in haystacks for needle in needles)


def first_match(text: str | None, patterns: Sequence[str]) -> str | None:
    """The first pattern in *patterns* that :func:`matches` *text*, or ``None``."""
    for pattern in patterns:
        if matches(text, pattern):
            return pattern
    return None


def all_matches(text: str | None, patterns: Sequence[str]) -> tuple[str, ...]:
    """Every pattern in *patterns* that :func:`matches` *text*.

    Used for :attr:`FilterResult.hits_by_pattern`, where the question is "is
    this pattern doing anything?" rather than "which one won". Frontier's
    ``Berlinhouse`` is redundant with ``Frontier Tower`` on most events, and
    reporting it as dead because another pattern got there first would train
    the reader to ignore the one signal that catches a typo.
    """
    return tuple(pattern for pattern in patterns if matches(text, pattern))


def category_matches(
    categories: Sequence[str], patterns: Sequence[str]
) -> tuple[tuple[str, str], ...]:
    """Every ``(category, pattern)`` pair that matches.

    Substring, like everything else here: Sudo Room's ``categories_exclude``
    entry ``noisebridge`` is meant to catch ``Noisebridge 5MoF`` as well as a
    bare ``Noisebridge``.
    """
    return tuple(
        (category, pattern)
        for category in categories
        for pattern in all_matches(category, patterns)
    )


def location_state(event: Event) -> LocationState:
    """Classify the **source's** ``LOCATION`` for this event.

    Reads :attr:`~pipeline.normalize.Event.location_name` only, never the
    resolved :attr:`~pipeline.normalize.Event.address` — see the module
    docstring for why matching an ``address_override`` against a registry filter
    is circular.
    """
    text = (event.location_name or "").strip()
    if not text:
        return LocationState.EMPTY
    if is_bare_url(text):
        return LocationState.URL_ONLY
    return LocationState.PRESENT


# --------------------------------------------------------------------------- engine


@dataclass
class _Tally:
    """Mutable accounting for one :func:`apply_filters` call."""

    drops_by_rule: dict[str, int] = field(default_factory=dict)
    hits_by_pattern: dict[str, dict[str, int]] = field(default_factory=dict)
    kept_on_missing_location: int = 0

    def configure(self, filters: Filters) -> None:
        """Seed a zero for every configured rule and pattern.

        The zeros are the point. ``health.json`` showing
        ``location_contains: {"Arts and Music": 0}`` is the only way that class
        of typo is ever going to be noticed.
        """
        for rule, patterns in (
            (FilterRule.TITLE_EXCLUDES, filters.title_excludes),
            (FilterRule.LOCATION_EXCLUDES, filters.location_excludes),
            (FilterRule.CATEGORIES_EXCLUDE, filters.categories_exclude),
            (FilterRule.TITLE_CONTAINS, filters.title_contains),
            (FilterRule.LOCATION_CONTAINS, filters.location_contains),
        ):
            if not patterns:
                continue
            self.drops_by_rule.setdefault(rule.value, 0)
            self.hits_by_pattern.setdefault(
                rule.value, {pattern: 0 for pattern in patterns}
            )
        if filters.location_contains:
            self.drops_by_rule.setdefault(FilterRule.LOCATION_MISSING.value, 0)

    def drop(self, rule: FilterRule) -> None:
        self.drops_by_rule[rule.value] = self.drops_by_rule.get(rule.value, 0) + 1

    def hit(self, rule: FilterRule, *patterns: str) -> None:
        bucket = self.hits_by_pattern.setdefault(rule.value, {})
        for pattern in patterns:
            bucket[pattern] = bucket.get(pattern, 0) + 1


def _verdict(
    event: Event, filters: Filters, tally: _Tally
) -> tuple[FilterRule, str | None, str] | None:
    """``(rule, pattern, detail)`` if the event is dropped, ``None`` if kept.

    Excludes first, then the keep-lists; the first rule to fire owns the drop.
    """
    hits = all_matches(event.title, filters.title_excludes)
    if hits:
        tally.hit(FilterRule.TITLE_EXCLUDES, *hits)
        return FilterRule.TITLE_EXCLUDES, hits[0], f"title contains {hits[0]!r}"

    state = location_state(event)
    location = event.location_name

    if state is LocationState.PRESENT:
        hits = all_matches(location, filters.location_excludes)
        if hits:
            tally.hit(FilterRule.LOCATION_EXCLUDES, *hits)
            return FilterRule.LOCATION_EXCLUDES, hits[0], f"location contains {hits[0]!r}"

    found = category_matches(event.categories, filters.categories_exclude)
    if found:
        tally.hit(FilterRule.CATEGORIES_EXCLUDE, *(pattern for _, pattern in found))
        category, hit = found[0]
        return (
            FilterRule.CATEGORIES_EXCLUDE,
            hit,
            f"source category {category!r} matches {hit!r}",
        )

    if filters.title_contains:
        hits = all_matches(event.title, filters.title_contains)
        if not hits:
            return (
                FilterRule.TITLE_CONTAINS,
                None,
                f"title matches none of {list(filters.title_contains)}",
            )
        tally.hit(FilterRule.TITLE_CONTAINS, *hits)

    if filters.location_contains:
        if state.is_missing:
            # The invariant, in code. Keeping a possibly-off-site event is a
            # visible, recoverable mistake; dropping a real one is neither.
            if not filters.location_allow_when_missing:
                return (
                    FilterRule.LOCATION_MISSING,
                    None,
                    f"location is {state.value} and location_allow_when_missing "
                    "is off",
                )
            tally.kept_on_missing_location += 1
            LOG.debug(
                "%s:%s filter keep %s: location is %s, kept by "
                "location_allow_when_missing (%r)",
                event.space_id,
                event.source_label,
                event.uid,
                state.value,
                event.title,
            )
        else:
            hits = all_matches(location, filters.location_contains)
            if not hits:
                return (
                    FilterRule.LOCATION_CONTAINS,
                    None,
                    f"location {location!r} matches none of "
                    f"{list(filters.location_contains)}",
                )
            tally.hit(FilterRule.LOCATION_CONTAINS, *hits)

    return None


def apply_filters(
    events: Iterable[Event],
    filters: Filters | None = None,
    *,
    space_id: str = "",
    source_label: str = "",
) -> FilterResult:
    """Apply one source's registry filters to its normalized events.

    The entry point. Runs between normalize and dedupe, per source, because
    ``filters`` is a property of a source in ``sources.yaml`` and nothing
    downstream can tell which feed an event came from once they are merged.

    Matching is **case-insensitive substring** and entity-insensitive; see the
    module docstring. ``None`` or an empty :class:`~pipeline.config.Filters`
    keeps everything, and still returns a countable result rather than the input
    list, so callers never branch on "were there filters?".
    """
    items = list(events)
    filters = filters or Filters()
    space_id = space_id or (items[0].space_id if items else "")
    source_label = source_label or (items[0].source_label if items else "")

    tally = _Tally()
    tally.configure(filters)

    kept: list[Event] = []
    dropped: list[Filtered] = []

    for event in items:
        verdict = _verdict(event, filters, tally)
        if verdict is None:
            kept.append(event)
            continue
        rule, pattern, detail = verdict
        tally.drop(rule)
        dropped.append(
            Filtered(
                rule=rule,
                pattern=pattern,
                space_id=event.space_id or space_id,
                source_label=event.source_label or source_label,
                uid=event.uid,
                title=event.title,
                location=event.location_name,
                detail=detail,
            )
        )
        # Every drop, with the rule that caused it. One grep for "filter drop"
        # answers "where did that event go?".
        LOG.debug(
            "%s:%s filter drop [%s] %s %r: %s",
            space_id,
            source_label,
            rule.value,
            event.uid,
            event.title,
            detail,
        )

    result = FilterResult(
        events=tuple(kept),
        dropped=tuple(dropped),
        space_id=space_id,
        source_label=source_label,
        drops_by_rule=tally.drops_by_rule,
        hits_by_pattern=tally.hits_by_pattern,
        kept_on_missing_location=tally.kept_on_missing_location,
    )
    _report(result, filters)
    return result


def _report(result: FilterResult, filters: Filters) -> None:
    """Say out loud what the filters did, loudly when it was drastic."""
    if filters.is_empty:
        return

    breakdown = ", ".join(
        f"{rule}={count}" for rule, count in sorted(result.drops_by_rule.items())
    )
    message = (
        "%s:%s filters kept %d of %d (%s); kept_on_missing_location=%d"
    )
    args = (
        result.space_id,
        result.source_label,
        result.kept_count,
        result.input_count,
        breakdown or "no rules fired",
        result.kept_on_missing_location,
    )

    if (
        result.input_count >= LOUD_DROP_MIN_EVENTS
        and result.drop_rate >= LOUD_DROP_RATE
    ):
        LOG.warning(
            message + " -- filters removed %.0f%% of this feed. Filters drop "
            "events silently; check the patterns against the values the source "
            "actually ships before trusting this.",
            *args,
            result.drop_rate * 100,
        )
    else:
        LOG.info(message, *args)

    for rule, pattern in result.dead_patterns:
        LOG.info(
            "%s:%s filter pattern %r under %s matched nothing. Verbatim strings "
            "from the feed are the only reliable source for these -- 'Arts and "
            "Music' never appears in the Frontier calendar, it is 'Arts & Music'.",
            result.space_id,
            result.source_label,
            pattern,
            rule,
        )


def filter_normalization(
    normalization: Normalization,
    filters: Filters | None = None,
) -> FilterResult:
    """Filter a :class:`~pipeline.normalize.Normalization`'s publishable events.

    The seam the nightly run uses: normalize returns one of these per source and
    the source's ``filters`` block applies to exactly its ``events``.
    Quarantined events are deliberately left alone — they are already withheld,
    and hiding a broken extraction behind a filter defeats the point of holding
    it.
    """
    return apply_filters(
        normalization.events,
        filters,
        space_id=normalization.space_id,
        source_label=normalization.source_label,
    )


__all__ = [
    "LOUD_DROP_MIN_EVENTS",
    "LOUD_DROP_RATE",
    "FilterResult",
    "FilterRule",
    "Filtered",
    "LocationState",
    "all_matches",
    "apply_filters",
    "category_matches",
    "filter_normalization",
    "first_match",
    "location_state",
    "matches",
]
