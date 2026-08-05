"""Tests for cross-source and cross-space dedupe (issue 0015).

**No test here touches the network.** Every event is hand-authored, and the
titles, labels, trust values and addresses are the ones recorded in
``sources.yaml`` and ``spaces/*.md`` on 2026-08-05 rather than plausible
inventions.

What is being defended:

**The 30-minute window is what makes comparing titles safe.** Maker Nexus has 76
distinct equipment-training titles repeating across 171 instances, and every
repetition is a genuinely different session. ``token_set_ratio`` scores two of
them at 100. If the window ever stops being enforced, that space collapses from
171 events to 76 and the feed still looks healthy — so the false-positive
regression is tested directly.

**Across spaces, similarity alone is never enough.** "Open Shop Night" is the
same three words at six different buildings. The cross-space rule needs 92
*and* an address match, and the test proves the address half is load-bearing by
holding the similarity at 100 and changing only the address.

**A merge never changes the surviving UID.** Every merge would otherwise
re-notify every subscriber, every night. Same hazard class as the UID-churn
invariant in ``CLAUDE.md``.

**Merges are data, not just log lines.** Issue 0017 reads the merge log into
``health.json``, and 85 is a number somebody picked: tuning it needs both UIDs
and the score that fired.
"""

from __future__ import annotations

import datetime as dt
import logging
import time

import pytest

from pipeline.config import CONTACT_ENV_VAR, SOURCES_YAML, load_registry
from pipeline.dedupe import (
    ADDRESS_THRESHOLD,
    CROSS_SPACE_THRESHOLD,
    DEFAULT_FIELD_PREFERENCES,
    MERGEABLE_FIELDS,
    START_WINDOW,
    WITHIN_SPACE_THRESHOLD,
    DedupeError,
    DedupePolicy,
    DedupeResult,
    FieldPreference,
    MergeKind,
    address_matches,
    address_similarity,
    dedupe,
    merge_pair,
    similarity,
    trust_map,
)
from pipeline.normalize import AddressSource, Event

NOW = dt.datetime(2026, 8, 5, 3, 15, tzinfo=dt.timezone.utc)
START = dt.datetime(2026, 9, 1, 19, 0, tzinfo=dt.timezone.utc)
PACIFIC = "America/Los_Angeles"

#: Verbatim from ``sources.yaml``: the Sudo Room Luma event that is actually at
#: The Box Shop, which is the registry's real cross-space collision.
HUDSON = "951 Hudson Ave, San Francisco, CA 94124"
SUDO = "2141 Broadway, Oakland, CA 94612"


# --------------------------------------------------------------------------- helpers


def event(
    title: str = "Open Shop Night",
    *,
    uid: str | None = None,
    space_id: str = "ace-makerspace",
    source_label: str = "tribe-rest",
    start: dt.datetime = START,
    end: dt.datetime | None = None,
    address: str | None = "3040 Telegraph Ave, Berkeley, CA 94705",
    location_name: str | None = None,
    url: str | None = None,
    price: str | None = None,
    description: str | None = None,
    categories: tuple[str, ...] = (),
    summary_line: str | None = None,
    rrule: str | None = None,
    off_site: bool = False,
    first_seen: dt.datetime | None = NOW,
    last_seen: dt.datetime | None = NOW,
) -> Event:
    """One canonical :class:`Event`, in the shape normalize would have left it."""
    return Event(
        uid=uid or f"{space_id}:{source_label}:{title}:{start.isoformat()}",
        space_id=space_id,
        source_label=source_label,
        title=title,
        start_utc=start,
        end_utc=end if end is not None else start + dt.timedelta(hours=2),
        tz=PACIFIC,
        location_name=location_name,
        address=address,
        url=url,
        price=price,
        description=description,
        categories=categories,
        summary_line=summary_line,
        rrule=rrule,
        first_seen=first_seen,
        last_seen=last_seen,
        content_hash="seed",
        address_source=AddressSource.SOURCE,
        off_site=off_site,
    )


#: Ace's registry trust values, verbatim.
ACE_TRUST = {
    ("ace-makerspace", "tribe-rest"): 100,
    ("ace-makerspace", "tribe-ics-list"): 90,
    ("ace-makerspace", "calendar-jsonld"): 50,
}


def uids(result: DedupeResult) -> set[str]:
    return {item.uid for item in result.events}


@pytest.fixture
def registry():
    """The real ``sources.yaml``, for the trust lookup. No network involved."""
    return load_registry(
        SOURCES_YAML, env={CONTACT_ENV_VAR: "https://maker-calendar.test/about"}
    )


# --------------------------------------------------------------------------- similarity


def test_similarity_normalizes_before_comparing():
    """Case, punctuation and unicode must not be three ways to miss a merge."""
    assert similarity("LASER FRYDAYS: Laser Cutter Training", "Laser Frydays — laser cutter training") == 100.0
    assert similarity("", "Open Shop Night") == 0.0
    assert similarity(None, None) == 0.0


def test_address_similarity_requires_agreeing_street_numbers():
    """Two buildings on one street differ only in the number."""
    assert address_matches(HUDSON, "951 Hudson Ave")
    assert address_matches("951 Hudson Ave", "951 Hudson Avenue, San Francisco")
    assert not address_matches(HUDSON, "953 Hudson Ave, San Francisco, CA 94124")
    assert address_similarity(HUDSON, "953 Hudson Ave, San Francisco") == 0.0


def test_a_bare_city_is_not_an_address():
    """Intersection scoring would otherwise match every event in Oakland."""
    assert not address_matches("Oakland", SUDO)
    assert not address_matches(None, SUDO)
    assert not address_matches("", "")


# --------------------------------------------------------------------------- within a space


def test_near_identical_events_ten_minutes_apart_merge_and_trust_wins():
    """The core rule: same space, inside the window, above 85."""
    winner = event(
        "Sewing 101 Bootcamp",
        uid="ace:rest-1",
        source_label="tribe-rest",
        description="Learn to sew.",
    )
    loser = event(
        "Sewing 101 Boot Camp",
        uid="ace:ics-1",
        source_label="tribe-ics-list",
        start=START + dt.timedelta(minutes=10),
        price="$85",
    )

    result = dedupe([winner, loser], trust=ACE_TRUST)

    assert result.event_count == 1
    assert result.merge_count == 1
    merged = result.events[0]
    assert merged.uid == "ace:rest-1"  # the trust-100 source survived
    assert merged.price == "$85"  # and took the loser's non-null field

    merge = result.merges[0]
    assert merge.kind is MergeKind.WITHIN_SPACE
    assert merge.winner_trust == 100 and merge.loser_trust == 90
    assert merge.score >= WITHIN_SPACE_THRESHOLD
    assert merge.start_delta_seconds == 600.0


def test_the_same_two_events_forty_five_minutes_apart_do_not_merge():
    """Outside the window is outside the rule, however identical the titles."""
    first = event("Sewing 101 Bootcamp", uid="ace:rest-1")
    second = event(
        "Sewing 101 Bootcamp",
        uid="ace:ics-1",
        source_label="tribe-ics-list",
        start=START + dt.timedelta(minutes=45),
    )

    result = dedupe([first, second], trust=ACE_TRUST)

    assert result.event_count == 2
    assert result.merge_count == 0
    # Never even scored: the window is checked before the fuzzy comparison.
    assert result.comparisons == 0


def test_similarity_just_under_the_threshold_does_not_merge():
    """82.1 is not 85. The bar is a bar, and the near miss is recorded."""
    first = event("Intro to Welding", uid="ace:rest-1")
    second = event(
        "Introduction to Welding",
        uid="ace:ics-1",
        source_label="tribe-ics-list",
        start=START + dt.timedelta(minutes=5),
    )
    assert 80.0 < similarity(first.title, second.title) < WITHIN_SPACE_THRESHOLD

    result = dedupe([first, second], trust=ACE_TRUST)

    assert result.event_count == 2
    assert result.merge_count == 0
    assert result.near_miss_count == 1
    miss = result.near_misses[0]
    assert miss.failed == "score"
    assert miss.threshold == WITHIN_SPACE_THRESHOLD
    assert {miss.left_uid, miss.right_uid} == {"ace:rest-1", "ace:ics-1"}


def test_non_null_loser_fields_fill_gaps_in_the_winner():
    """A merge takes what the winner lacks and overwrites nothing it has."""
    winner = event(
        "Open Shop Night",
        uid="ace:rest-1",
        url="https://acemakerspace.org/events/open-shop",
        description=None,
        price=None,
        categories=(),
    )
    loser = event(
        "Open Shop Night",
        uid="ace:ics-1",
        source_label="tribe-ics-list",
        start=START + dt.timedelta(minutes=3),
        url="https://example.invalid/other",
        description="Come make things.",
        price="Free",
        categories=("Open Shop",),
        rrule="FREQ=WEEKLY",
    )

    result = dedupe([winner, loser], trust=ACE_TRUST)
    merged = result.events[0]

    assert merged.description == "Come make things."
    assert merged.price == "Free"
    assert merged.categories == ("Open Shop",)
    assert merged.rrule == "FREQ=WEEKLY"
    # Present on the winner: not overwritten.
    assert merged.url == "https://acemakerspace.org/events/open-shop"
    assert set(result.merges[0].fields_filled) == {
        "description",
        "price",
        "categories",
        "rrule",
    }


def test_the_winners_uid_is_unchanged_by_a_merge():
    """UID churn re-notifies every subscriber. This is the whole invariant."""
    winner = event("Open Shop Night", uid="ace:rest-1", description=None)
    loser = event(
        "Open Shop Night",
        uid="ace:ics-1",
        source_label="tribe-ics-list",
        start=START + dt.timedelta(minutes=7),
        description="Come make things.",
    )

    merged, filled, preferred = merge_pair(winner, loser)
    assert merged.uid == winner.uid == "ace:rest-1"
    assert filled == ("description",)
    assert preferred == ()

    result = dedupe([winner, loser], trust=ACE_TRUST)
    assert result.events[0].uid == "ace:rest-1"
    assert result.merges[0].winner_uid == "ace:rest-1"


def test_a_merge_recomputes_the_content_hash_only_when_it_has_to():
    """The published content changed, so the change fingerprint must too."""
    winner = event("Open Shop Night", uid="ace:rest-1", description=None)
    loser = event(
        "Open Shop Night",
        uid="ace:ics-1",
        source_label="tribe-ics-list",
        start=START + dt.timedelta(minutes=7),
        description="Come make things.",
    )
    merged, _, _ = merge_pair(winner, loser)
    assert merged.content_hash != "seed"

    # price is not part of the hash; nothing to recompute.
    untouched, _, _ = merge_pair(
        event("Open Shop Night", uid="ace:rest-1", price=None),
        event(
            "Open Shop Night",
            uid="ace:ics-1",
            source_label="tribe-ics-list",
            price="$45",
        ),
    )
    assert untouched.content_hash == "seed"


def test_a_merge_keeps_the_earlier_first_seen():
    """RSS pubDate reads first_seen. A merge is not a new event."""
    earlier = NOW - dt.timedelta(days=9)
    winner = event("Open Shop Night", uid="ace:rest-1", first_seen=NOW)
    loser = event(
        "Open Shop Night",
        uid="ace:ics-1",
        source_label="tribe-ics-list",
        first_seen=earlier,
    )
    merged, _, _ = merge_pair(winner, loser)
    assert merged.first_seen == earlier


# --------------------------------------------------------------------------- the regression


def test_repeating_maker_nexus_training_titles_do_not_merge():
    """**The false-positive regression.**

    76 distinct equipment-training titles across 171 instances. These two score
    100 against each other and are two genuinely bookable sessions a week
    apart. A title-only rule would publish 76 events and look healthy.
    """
    titles = [
        "Laser Cutter (Equipment Training)",
        "CNC Router (Equipment Training)",
        "Metal Lathe (Equipment Training)",
    ]
    events = [
        event(
            title,
            uid=f"maker-nexus:{title}:{day}",
            space_id="maker-nexus",
            source_label="amilia-published-classes",
            start=START + dt.timedelta(days=day, hours=index),
            address=None,
        )
        for index, title in enumerate(titles)
        for day in range(7)
    ]
    assert similarity(events[0].title, events[1].title) == 100.0

    result = dedupe(events, trust={("maker-nexus", "amilia-published-classes"): 100})

    assert result.event_count == len(events) == 21
    assert result.merge_count == 0


# --------------------------------------------------------------------------- across spaces


def _cross_space_pair(*, box_shop_address: str = HUDSON):
    """The registry's real case: Sudo Room's Luma carrying a Box Shop event."""
    box_shop = event(
        "Foundry Open House",
        uid="the-box-shop:luma-1",
        space_id="the-box-shop",
        source_label="luma",
        address=box_shop_address,
    )
    sudo = event(
        "Foundry Open House",
        uid="sudo-room:luma-1",
        space_id="sudo-room",
        source_label="luma",
        start=START + dt.timedelta(minutes=15),
        address=HUDSON,
        off_site=True,
        price="$20",
    )
    return box_shop, sudo


def test_cross_space_merge_needs_similarity_and_an_address_match():
    box_shop, sudo = _cross_space_pair()
    trust = {("the-box-shop", "luma"): 100, ("sudo-room", "luma"): 90}

    result = dedupe([box_shop, sudo], trust=trust)

    assert result.event_count == 1
    merge = result.merges[0]
    assert merge.kind is MergeKind.CROSS_SPACE
    assert merge.score >= CROSS_SPACE_THRESHOLD
    assert merge.address_score is not None and merge.address_score >= ADDRESS_THRESHOLD
    assert result.events[0].uid == "the-box-shop:luma-1"
    assert result.events[0].price == "$20"


def test_similarity_alone_is_not_enough_across_spaces():
    """Identical titles, different buildings. "Open Shop Night" is everywhere."""
    box_shop, sudo = _cross_space_pair(box_shop_address=SUDO)
    assert similarity(box_shop.title, sudo.title) == 100.0

    result = dedupe(
        [box_shop, sudo],
        trust={("the-box-shop", "luma"): 100, ("sudo-room", "luma"): 90},
    )

    assert result.event_count == 2
    assert result.merge_count == 0
    assert result.near_miss_count == 1
    assert result.near_misses[0].failed == "address"


def test_cross_space_similarity_between_the_two_thresholds_does_not_merge():
    """85 is the within-space bar. Across spaces the bar is 92."""
    left = event(
        "Glass Fusing and Slumping",
        uid="the-crucible:blob-1",
        space_id="the-crucible",
        source_label="course-catalog-blob",
        address=HUDSON,
    )
    right = event(
        "Glass Fusing Slumping Workshop",
        uid="the-box-shop:luma-1",
        space_id="the-box-shop",
        source_label="luma",
        start=START + dt.timedelta(minutes=5),
        address=HUDSON,
    )
    score = similarity(left.title, right.title)
    assert WITHIN_SPACE_THRESHOLD < score < CROSS_SPACE_THRESHOLD

    result = dedupe([left, right])
    assert result.event_count == 2
    assert result.merge_count == 0


def test_cross_space_can_be_turned_off_wholesale():
    box_shop, sudo = _cross_space_pair()
    result = dedupe([box_shop, sudo], policy=DedupePolicy(cross_space=False))
    assert result.event_count == 2


# --------------------------------------------------------------------------- Ace


def test_aces_byte_equivalent_feeds_collapse_cleanly():
    """``tribe_rest`` and ``tribe-ics-list`` carry identical content.

    The registry says not to ingest both. Dedupe is the backstop if someone
    enables both anyway, and the backstop has to leave the REST copy standing.
    """
    titles = [
        "Sewing 101 Bootcamp",
        "Open Shop Night",
        "Woodshop Safety Orientation",
        "Intro to Blacksmithing",
    ]
    rest = [
        event(
            title,
            uid=f"ace-makerspace:rest:{index}",
            source_label="tribe-rest",
            start=START + dt.timedelta(hours=3 * index),
            description=f"{title} at Ace.",
        )
        for index, title in enumerate(titles)
    ]
    ics = [
        event(
            title,
            uid=f"ace-makerspace:ics:{index}",
            source_label="tribe-ics-list",
            start=START + dt.timedelta(hours=3 * index),
            description=f"{title} at Ace.",
        )
        for index, title in enumerate(titles)
    ]

    result = dedupe(rest + ics, trust=ACE_TRUST)

    assert result.event_count == 4
    assert result.merge_count == 4
    assert uids(result) == {f"ace-makerspace:rest:{index}" for index in range(4)}
    assert all(merge.winner_trust == 100 for merge in result.merges)
    assert all(merge.kind is MergeKind.WITHIN_SPACE for merge in result.merges)


def test_three_ace_feeds_collapse_to_one_record_per_event():
    """Ace has REST, ICS and JSON-LD. A cluster of three reduces to the best."""
    events = [
        event("Open Shop Night", uid="ace:rest", source_label="tribe-rest"),
        event(
            "Open Shop Night",
            uid="ace:ics",
            source_label="tribe-ics-list",
            start=START + dt.timedelta(minutes=4),
        ),
        event(
            "Open Shop Night",
            uid="ace:jsonld",
            source_label="calendar-jsonld",
            start=START + dt.timedelta(minutes=8),
            price="$0",
        ),
    ]
    result = dedupe(events, trust=ACE_TRUST)

    assert result.event_count == 1
    assert result.merge_count == 2
    assert result.events[0].uid == "ace:rest"
    assert result.events[0].price == "$0"


def test_a_chain_longer_than_the_window_does_not_collapse_end_to_end():
    """A~B and B~C must not merge A with C an hour apart."""
    events = [
        event("Open Shop Night", uid="ace:a", start=START),
        event(
            "Open Shop Night",
            uid="ace:b",
            source_label="tribe-ics-list",
            start=START + dt.timedelta(minutes=29),
        ),
        event(
            "Open Shop Night",
            uid="ace:c",
            source_label="calendar-jsonld",
            start=START + dt.timedelta(minutes=58),
        ),
    ]
    result = dedupe(events, trust=ACE_TRUST)

    # A absorbs B (29 min); C is 58 minutes from A and survives on its own.
    assert result.event_count == 2
    assert uids(result) == {"ace:a", "ace:c"}
    assert result.merge_count == 1


# --------------------------------------------------------------------------- field policy


def test_maker_nexus_json_cache_wins_price_against_the_higher_trust_gcal():
    """A field preference outranks trust for that field, and only that field."""
    gcal = event(
        "Community Laser Night",
        uid="maker-nexus:gcal-1",
        space_id="maker-nexus",
        source_label="amilia-published-classes",
        price="see Amilia",
        description="From the gCal.",
        address=None,
    )
    cache = event(
        "Community Laser Night",
        uid="maker-nexus:cache-1",
        space_id="maker-nexus",
        source_label="amilia-community-events-cache",
        start=START + dt.timedelta(minutes=6),
        price="$25",
        description="From the cache.",
        address=None,
    )
    trust = {
        ("maker-nexus", "amilia-published-classes"): 100,
        ("maker-nexus", "amilia-community-events-cache"): 90,
    }

    result = dedupe([gcal, cache], trust=trust)
    merged = result.events[0]

    assert merged.uid == "maker-nexus:gcal-1"  # gCal still wins the record
    assert merged.price == "$25"  # but the cache wins price
    assert merged.description == "From the gCal."  # and nothing else
    assert result.merges[0].fields_preferred == ("price",)


def test_the_crucible_api_wins_description_and_the_blob_keeps_its_categories():
    """The API's categories are polluted with per-product entries.

    So the blob owns ``categories`` even when it has none — the preference
    blocks the API from supplying the field at all, rather than merely losing
    to a value that happens to be there.
    """
    blob = event(
        "Glass Fusing &amp; Slumping",
        uid="the-crucible:blob-1",
        space_id="the-crucible",
        source_label="course-catalog-blob",
        description=None,
        price=None,
        categories=(),
        address="1260 7th St, Oakland, CA 94607",
    )
    api = event(
        "Glass Fusing &amp; Slumping",
        uid="the-crucible:api-1",
        space_id="the-crucible",
        source_label="woocommerce-store-api",
        start=START + dt.timedelta(minutes=12),
        description="Fuse and slump glass in the kiln.",
        price="$395",
        categories=("Glass Fusing &amp; Slumping", "Glass"),
        address="1260 7th St, Oakland, CA 94607",
    )
    trust = {
        ("the-crucible", "course-catalog-blob"): 100,
        ("the-crucible", "woocommerce-store-api"): 90,
    }

    result = dedupe([blob, api], trust=trust)
    merged = result.events[0]

    assert merged.uid == "the-crucible:blob-1"
    assert merged.description == "Fuse and slump glass in the kiln."
    assert merged.price == "$395"
    assert merged.categories == ()  # the polluted list never lands
    assert set(result.merges[0].fields_preferred) == {"description", "price"}


def test_the_default_preferences_only_name_fields_the_record_carries():
    """A preference on a field Event lacks would silently do nothing."""
    for preference in DEFAULT_FIELD_PREFERENCES:
        for name in preference.fields:
            assert name in MERGEABLE_FIELDS


def test_a_preference_naming_an_unknown_field_is_rejected_loudly():
    with pytest.raises(DedupeError, match="capacity"):
        DedupePolicy(
            field_preferences=(
                FieldPreference("maker-nexus", "amilia-community-events-cache", ("capacity",)),
            )
        )


# --------------------------------------------------------------------------- the log


def test_the_merge_log_records_both_uids_and_the_score(caplog):
    """85 is a number somebody picked; this is how it gets tuned."""
    winner = event("Sewing 101 Bootcamp", uid="ace:rest-1")
    loser = event(
        "Sewing 101 Boot Camp",
        uid="ace:ics-1",
        source_label="tribe-ics-list",
        start=START + dt.timedelta(minutes=10),
    )

    with caplog.at_level(logging.INFO, logger="pipeline.dedupe"):
        result = dedupe([winner, loser], trust=ACE_TRUST)

    merge = result.merges[0]
    assert merge.winner_uid == "ace:rest-1"
    assert merge.loser_uid == "ace:ics-1"
    assert merge.score == pytest.approx(97.4, abs=0.5)

    payload = merge.as_dict()
    assert payload["winner"]["uid"] == "ace:rest-1"
    assert payload["loser"]["uid"] == "ace:ics-1"
    assert payload["kind"] == "within_space"
    assert payload["score"] == pytest.approx(97.4, abs=0.5)

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "ace:rest-1" in logged and "ace:ics-1" in logged
    assert "score=" in logged


def test_the_summary_is_json_ready_for_health_json():
    box_shop, sudo = _cross_space_pair()
    events = [
        box_shop,
        sudo,
        event("Open Shop Night", uid="ace:rest-1"),
        event(
            "Open Shop Night",
            uid="ace:ics-1",
            source_label="tribe-ics-list",
            start=START + dt.timedelta(minutes=2),
        ),
    ]
    result = dedupe(events, trust=ACE_TRUST)
    summary = result.summary()

    assert summary["input"] == 4
    assert summary["kept"] == 2
    assert summary["merged"] == 2
    assert summary["by_kind"] == {"within_space": 1, "cross_space": 1}
    assert summary["thresholds"]["within_space"] == WITHIN_SPACE_THRESHOLD
    assert summary["thresholds"]["start_window_minutes"] == 30
    assert len(summary["merges"]) == 2

    import json

    json.dumps(summary)  # must not raise


def test_trust_map_is_keyed_on_the_label_events_actually_carry(registry):
    table = trust_map(registry)
    assert table[("ace-makerspace", "tribe-rest")] == 100
    assert table[("ace-makerspace", "tribe-ics-list")] == 90
    assert table[("maker-nexus", "amilia-published-classes")] == 100


def test_dedupe_is_a_no_op_on_an_empty_set():
    result = dedupe([])
    assert result.event_count == 0
    assert result.merge_count == 0
    assert result.summary()["input"] == 0


def test_dedupe_preserves_input_order_for_survivors():
    events = [
        event("Alpha", uid="ace:a", start=START + dt.timedelta(hours=5)),
        event("Beta", uid="ace:b", start=START),
        event("Gamma", uid="ace:c", start=START + dt.timedelta(hours=2)),
    ]
    result = dedupe(events, trust=ACE_TRUST)
    assert [item.uid for item in result.events] == ["ace:a", "ace:b", "ace:c"]


# --------------------------------------------------------------------------- performance


def test_three_thousand_events_do_not_become_quadratic():
    """Maker Nexus alone contributes 3645 events before the horizon clip.

    A loose bound on purpose: the point is not the wall clock on this machine,
    it is that the blocking exists at all. Without the start-time window this
    is 4.5 million ``token_set_ratio`` calls and the assertion on
    ``comparisons`` fails long before the timing does.
    """
    count = 3000
    events = [
        event(
            f"{['Laser Cutter', 'CNC Router', 'Metal Lathe', 'Woodshop'][index % 4]} "
            "(Equipment Training)",
            uid=f"maker-nexus:{index}",
            space_id="maker-nexus",
            source_label="amilia-published-classes",
            start=START + dt.timedelta(minutes=17 * index),
            address=None,
        )
        for index in range(count)
    ]

    started = time.perf_counter()
    result = dedupe(events, trust={("maker-nexus", "amilia-published-classes"): 100})
    elapsed = time.perf_counter() - started

    assert result.input_count == count
    assert result.event_count == count  # 17 minutes apart, all distinct titles
    # Quadratic would be ~4.5M. The window admits at most one neighbour each.
    assert result.comparisons < 10 * count
    assert elapsed < 10.0


def test_a_dense_cluster_still_terminates_and_keeps_one_record():
    """The pathological shape: 200 identical events at the same instant."""
    events = [
        event(
            "Open Shop Night",
            uid=f"ace:{index}",
            source_label="tribe-rest" if index == 0 else "tribe-ics-list",
        )
        for index in range(200)
    ]
    result = dedupe(events, trust=ACE_TRUST)
    assert result.event_count == 1
    assert result.events[0].uid == "ace:0"
    assert result.merge_count == 199


def test_the_start_window_is_the_documented_thirty_minutes():
    assert START_WINDOW == dt.timedelta(minutes=30)
    assert WITHIN_SPACE_THRESHOLD == 85.0
    assert CROSS_SPACE_THRESHOLD == 92.0
