"""Tests for the registry filters (issue 0010).

**No test here touches the network.** Same rule as the adapter and normalize
suites: every input is hand-authored, and the location strings are the verbatim
values recorded in ``spaces/frontier-makerspace.md`` on 2026-08-05 rather than
plausible-looking inventions. That distinction is the entire subject of this
issue — the *guessed* Frontier filter kept 9 of 262 events because ``Arts and
Music`` never appears in that feed.

What is being defended:

**A filter drop is never silent.** Every dropped event is logged with the rule
that dropped it and counted under that rule, and the counts include configured
rules that fired zero times, because a zero is how a mistyped pattern shows up.

**An event whose ``LOCATION`` is missing or a bare URL survives a
``location_contains`` filter by default.** 40 of Frontier Tower's 262 events
(15%) set ``LOCATION`` to a bare ``luma.com/event/…`` URL when the host hides
the venue, and several of those are the makerspace events this project exists
to publish. This is the regression the issue was filed for, so it is tested
from both sides: kept when ``location_allow_when_missing`` is on (the default),
dropped only when a registry entry explicitly asks for that.

**A resolved ``address_override`` is not matched.** Humanmade's partner
calendar has no ``LOCATION`` at all; if filters matched the override, a
``location_contains: ["Humanmade"]`` there would match every event and look
like a working filter while actually being a no-op.
"""

from __future__ import annotations

import datetime as dt
import logging

import pytest

from pipeline.config import Filters
from pipeline.normalize import AddressSource, Event, Normalization
from pipeline.filters import (
    FilterResult,
    FilterRule,
    LocationState,
    apply_filters,
    filter_normalization,
    first_match,
    location_state,
    matches,
)

#: The source-survey date, at the hour launchd runs the job.
NOW = dt.datetime(2026, 8, 5, 3, 15, tzinfo=dt.timezone.utc)
START = dt.datetime(2026, 9, 1, 19, 0, tzinfo=dt.timezone.utc)

PACIFIC = "America/Los_Angeles"


# --------------------------------------------------------------------------- helpers


def event(
    title: str = "Open Shop Night",
    *,
    location: str | None = "995 Market St, San Francisco, CA 94103, USA",
    uid: str | None = None,
    space_id: str = "frontier-makerspace",
    source_label: str = "luma-frontiertower",
    categories: tuple[str, ...] = (),
    address: str | None = None,
    address_source: AddressSource = AddressSource.SOURCE,
    start: dt.datetime = START,
) -> Event:
    """One canonical :class:`Event`, in the shape normalize would have left it."""
    return Event(
        uid=uid or f"{space_id}:{abs(hash((title, location, start)))}",
        space_id=space_id,
        source_label=source_label,
        title=title,
        start_utc=start,
        end_utc=start + dt.timedelta(hours=2),
        tz=PACIFIC,
        location_name=location,
        address=address if address is not None else location,
        categories=categories,
        address_source=address_source,
        first_seen=NOW,
        last_seen=NOW,
    )


def titles(result: FilterResult) -> list[str]:
    return [item.title for item in result]


#: The real Frontier registry filter, verbatim from ``sources.yaml``.
FRONTIER = Filters(
    location_contains=["Frontier Tower", "995 Market", "Berlinhouse"],
    location_allow_when_missing=True,
    title_excludes=["Hold -", "TBA (", "Placeholder"],
)

#: The originally-guessed one, kept as a test subject: it matched 9 of 262.
FRONTIER_GUESS = Filters(
    location_contains=["Makerspace", "Arts and Music", "Robotics"],
)


def frontier_feed() -> list[Event]:
    """A synthetic Frontier calendar built only from observed values.

    Locations are verbatim from the 262-event ICS pull tabulated in
    ``spaces/frontier-makerspace.md``: the two commonest building strings, the
    emoji variant, the bare street address, a floor string, the three off-site
    venues, and the ``luma.com/event/…`` URL that 15% of the feed carries —
    here on the three real makerspace events named in that file.
    """
    return [
        event("Frontier Tower Community Dinner", location="Frontier Tower | Berlinhouse, 995 Market St, San Francisco, CA 94103, USA"),
        event("Monthly Robotics Meetup", location="Frontier Tower @ Hard Tech & Robotics 995 Market Street, San Francisco"),
        event("MASS ARTS 5", location="Frontier Tower @ Arts & Music 995 Market Street, San Francisco"),
        event("Otherminds Community Meet", location="Frontier Tower \U0001f9d1‍\U0001f680, 995 Market St, San Francisco, CA 94103, USA"),
        event("16th Floor Coffee Meetup", location="Frontier Tower @ Lounge / Floor 16 995 Market Street, San Francisco"),
        event("Ethereum House Office Hours", location="995 Market St, San Francisco, California"),
        event("Makerspace Floor Welcome", location="Frontier Tower | Berlinhouse FL 7 — Makerspace"),
        # The hidden-address events. All three are named in the research file.
        event("LASER FRYDAYS: Laser Cutter Training", location="https://luma.com/event/evt-8Kq2wVn"),
        event("Frontier Makerspace All-Hands & Demo Night", location="https://luma.com/event/evt-3Bd9pLm"),
        event("The SF Bay Area LeRobot Hackathon", location="https://luma.com/event/evt-7Zx1cRt"),
        # Off-site, must go.
        event("Denver Satellite Demo", location="2121 Larimer St, Denver, CO 80205, USA"),
        event("Partner Mixer", location="2 Embarcadero Ctr, San Francisco, CA 94111, USA"),
        event("Community Potluck", location="466 Eddy St, San Francisco, CA 94109, USA"),
        # Administrative noise, dropped by title.
        event("Hold - Floor 2 buildout", location="Frontier Tower @ Spaceship / Floor 2 995 Market Street, San Francisco"),
        event("TBA (speaker confirming)", location="Frontier Tower @ Spaceship 995 Market Street, San Francisco"),
        event("Placeholder for October summit", location="995 Market St, San Francisco, CA 94103, USA"),
    ]


# --------------------------------------------------------------------------- matching


def test_matching_is_case_insensitive_substring() -> None:
    """Stated in the docstring, so it is tested rather than assumed."""
    assert matches("FRONTIER TOWER @ MAKERSPACE / FLOOR 7", "Frontier Tower")
    assert matches("frontier tower | berlinhouse", "BERLINHOUSE")
    assert matches("Laser Cutter (Equipment Training)", "(equipment training)")
    assert not matches("995 Market St", "Market Street")


def test_case_insensitivity_holds_through_the_engine() -> None:
    feed = [
        event("Open Shop", location="995 MARKET ST, SAN FRANCISCO, CA 94103, USA"),
        event("Off Site", location="2121 Larimer St, Denver, CO 80205, USA"),
    ]
    result = apply_filters(feed, Filters(location_contains=["995 market"]))
    assert titles(result) == ["Open Shop"]


def test_an_empty_pattern_never_matches() -> None:
    """An empty string is a substring of everything; that way lies an empty feed."""
    assert not matches("anything at all", "")
    assert first_match("anything at all", ["", "  "]) is None


def test_an_html_escaped_value_matches_a_filter_written_with_an_ampersand() -> None:
    """The Crucible's blob is ``Glass Fusing &amp; Slumping``, never ``&``."""
    stored = "Glass Fusing &amp; Slumping"
    assert matches(stored, "Glass Fusing & Slumping")
    assert matches(stored, "Glass Fusing &amp; Slumping")
    # ...and the reverse, so a registry author cannot get it wrong either way.
    assert matches("Glass Fusing & Slumping", "Glass Fusing &amp; Slumping")


def test_the_escaped_form_matches_through_the_engine() -> None:
    feed = [
        event("Glass Fusing &amp; Slumping", location=None),
        event("Youth Blacksmithing", location=None),
    ]
    result = apply_filters(feed, Filters(title_excludes=["Glass Fusing & Slumping"]))
    assert titles(result) == ["Youth Blacksmithing"]
    assert result.drops_by_rule["title_excludes"] == 1


def test_arts_and_music_is_not_arts_ampersand_music() -> None:
    """The bug this issue exists to make visible, as an assertion.

    Entity handling must not be so eager that it papers over a genuinely wrong
    pattern: ``Arts and Music`` is a different string from ``Arts & Music`` and
    must still miss.
    """
    assert not matches("Frontier Tower @ Arts & Music 995 Market Street", "Arts and Music")


# --------------------------------------------------------------------------- location


def test_location_contains_keeps_matches_and_drops_non_matches() -> None:
    feed = [
        event("Robot Fights", location="Frontier Tower @ Hard Tech & Robotics 995 Market Street, San Francisco"),
        event("Denver Satellite Demo", location="2121 Larimer St, Denver, CO 80205, USA"),
    ]
    result = apply_filters(feed, FRONTIER)

    assert titles(result) == ["Robot Fights"]
    assert result.dropped[0].rule is FilterRule.LOCATION_CONTAINS
    assert result.dropped[0].pattern is None  # no single pattern causes a keep-miss
    assert "2121 Larimer" in result.dropped[0].detail


def test_location_excludes_drops_matches() -> None:
    feed = [
        event("Open Shop", location="Frontier Tower, 995 Market St"),
        event("Denver Satellite Demo", location="2121 Larimer St, Denver, CO 80205, USA"),
    ]
    result = apply_filters(feed, Filters(location_excludes=["Denver"]))

    assert titles(result) == ["Open Shop"]
    assert result.drops_by_rule == {"location_excludes": 1}
    assert result.hits_by_pattern["location_excludes"]["Denver"] == 1


# ---- the regression this issue exists for ---------------------------------


@pytest.mark.parametrize(
    "location",
    ["https://luma.com/event/evt-8Kq2wVn", None, "", "   "],
    ids=["luma-url", "none", "empty", "whitespace"],
)
def test_a_missing_location_survives_when_allow_when_missing_is_on(location) -> None:
    """15% of Frontier Tower's feed, including real makerspace events."""
    feed = [event("LASER FRYDAYS: Laser Cutter Training", location=location)]
    result = apply_filters(
        feed,
        Filters(
            location_contains=["Frontier Tower", "995 Market", "Berlinhouse"],
            location_allow_when_missing=True,
        ),
    )

    assert titles(result) == ["LASER FRYDAYS: Laser Cutter Training"]
    assert result.dropped == ()
    assert result.kept_on_missing_location == 1


@pytest.mark.parametrize(
    "location",
    ["https://luma.com/event/evt-8Kq2wVn", None, "", "   "],
    ids=["luma-url", "none", "empty", "whitespace"],
)
def test_a_missing_location_is_dropped_when_allow_when_missing_is_off(location) -> None:
    """The same events, with the flag a registry entry explicitly turned off."""
    feed = [event("LASER FRYDAYS: Laser Cutter Training", location=location)]
    result = apply_filters(
        feed,
        Filters(
            location_contains=["Frontier Tower", "995 Market", "Berlinhouse"],
            location_allow_when_missing=False,
        ),
    )

    assert titles(result) == []
    assert result.dropped[0].rule is FilterRule.LOCATION_MISSING
    assert "location_allow_when_missing is off" in result.dropped[0].detail
    assert result.drops_by_rule["location_missing"] == 1
    # ...and it is NOT charged to location_contains, because the fix is different.
    assert result.drops_by_rule["location_contains"] == 0
    assert result.kept_on_missing_location == 0


def test_the_registry_default_keeps_a_missing_location() -> None:
    """``Filters()`` with no opinion must keep, per the CLAUDE.md invariant."""
    assert Filters().location_allow_when_missing is True

    result = apply_filters(
        [event("Hidden Address Workshop", location="https://luma.com/event/evt-1")],
        Filters(location_contains=["995 Market"]),
    )
    assert result.kept_count == 1
    assert result.kept_on_missing_location == 1


def test_the_two_missing_states_are_distinguished() -> None:
    """"Luma hid the address" and "this feed has no venue field" differ."""
    hidden = event("Hidden", location="https://luma.com/event/evt-1")
    blank = event("Blank", location=None)
    real = event("Real", location="995 Market St, San Francisco")

    assert location_state(hidden) is LocationState.URL_ONLY
    assert location_state(blank) is LocationState.EMPTY
    assert location_state(real) is LocationState.PRESENT
    assert LocationState.URL_ONLY.is_missing and LocationState.EMPTY.is_missing
    assert not LocationState.PRESENT.is_missing

    strict = Filters(location_contains=["995 Market"], location_allow_when_missing=False)
    result = apply_filters([hidden, blank, real], strict)
    details = {item.title: item.detail for item in result.dropped}
    assert "url_only" in details["Hidden"]
    assert "empty" in details["Blank"]


def test_an_address_override_is_not_matched_against_a_location_filter() -> None:
    """The Humanmade trap: matching our own override makes the filter a no-op.

    Issue 0009 fills ``address`` from ``space.address_override`` when the source
    gives no ``LOCATION``. If filters matched that, every event in such a feed
    would "match" and the filter would silently do nothing at all.
    """
    partner = event(
        "Soldering 101 @ Humanmade",
        location=None,
        address="Humanmade, 570 9th St, San Francisco, CA 94103",
        address_source=AddressSource.OVERRIDE,
        space_id="humanmade",
        source_label="partner-gcal",
    )
    result = apply_filters(
        [partner],
        Filters(location_contains=["Humanmade"], location_allow_when_missing=False),
    )
    assert result.kept_count == 0
    assert result.dropped[0].rule is FilterRule.LOCATION_MISSING

    # ...which is why the registry filters that calendar on the title instead.
    kept = apply_filters([partner], Filters(title_contains=["@ Humanmade"]))
    assert kept.kept_count == 1


def test_a_location_the_override_replaced_is_still_matched() -> None:
    """An overridden *address* does not make a present ``LOCATION`` invisible."""
    stale = event(
        "Sudo Sesh",
        location="549 48th St, Oakland, CA 94609",
        address="Omni Commons, 4799 Shattuck Ave, Oakland, CA 94609",
        address_source=AddressSource.OVERRIDE,
        space_id="sudo-room",
    )
    assert location_state(stale) is LocationState.PRESENT
    assert apply_filters([stale], Filters(location_contains=["549 48th"])).kept_count == 1
    assert apply_filters([stale], Filters(location_contains=["Shattuck"])).kept_count == 0


# --------------------------------------------------------------------------- title


def test_title_excludes_drops_by_substring() -> None:
    """Maker Nexus: 76 distinct checkout titles across 171 instances."""
    feed = [
        event("Laser Cutter Safety Checkout (Equipment Training)", location=None),
        event("CNC Router Basics (Equipment Training)", location=None),
        event("Open House", location=None),
    ]
    result = apply_filters(
        feed,
        Filters(title_excludes=["(Equipment Training)"]),
        space_id="maker-nexus",
        source_label="gcal-classes",
    )

    assert titles(result) == ["Open House"]
    assert result.drops_by_rule == {"title_excludes": 2}
    assert result.hits_by_pattern["title_excludes"]["(Equipment Training)"] == 2
    assert all(item.rule is FilterRule.TITLE_EXCLUDES for item in result.dropped)


def test_title_contains_keeps_only_matches() -> None:
    feed = [
        event("Soldering 101 @ Humanmade", location=None),
        event("Soldering 101 @ Some Other Space", location=None),
    ]
    result = apply_filters(feed, Filters(title_contains=["@ Humanmade"]))

    assert titles(result) == ["Soldering 101 @ Humanmade"]
    assert result.dropped[0].rule is FilterRule.TITLE_CONTAINS
    assert result.hits_by_pattern["title_contains"]["@ Humanmade"] == 1


# --------------------------------------------------------------------------- categories


def test_categories_exclude_drops_by_source_category() -> None:
    """Sudo Room's feed carries cross-posted third-party events."""
    feed = [
        event("Sudo Sesh", location=None, categories=("sudo room", "hack night")),
        event("Noisebridge 5MoF", location=None, categories=("Noisebridge", "talks")),
        event("Bike Party", location=None, categories=("events happening elsewhere",)),
    ]
    result = apply_filters(
        feed,
        Filters(categories_exclude=["events happening elsewhere", "noisebridge"]),
        space_id="sudo-room",
        source_label="wp-ics",
    )

    assert titles(result) == ["Sudo Sesh"]
    assert result.drops_by_rule == {"categories_exclude": 2}
    assert result.hits_by_pattern["categories_exclude"]["noisebridge"] == 1
    assert "Noisebridge" in result.dropped[0].detail  # the category, verbatim


def test_source_categories_are_matched_case_insensitively() -> None:
    feed = [event("Cross-post", location=None, categories=("Events Happening Elsewhere",))]
    result = apply_filters(feed, Filters(categories_exclude=["events happening elsewhere"]))
    assert result.kept_count == 0


# --------------------------------------------------------------------------- counts


def test_per_rule_counts_are_accurate_and_sum_to_the_drops() -> None:
    result = apply_filters(frontier_feed(), FRONTIER)

    assert result.drops_by_rule == {
        "title_excludes": 3,
        "location_contains": 3,
        "location_missing": 0,
    }
    assert sum(result.drops_by_rule.values()) == result.dropped_count
    assert result.input_count == result.kept_count + result.dropped_count == 16


def test_configured_rules_report_a_zero_rather_than_going_missing() -> None:
    """A rule that fired zero times must still appear; the zero is the signal."""
    result = apply_filters(
        [event("Open Shop", location="995 Market St")],
        Filters(location_contains=["995 Market"], title_excludes=["Hold -"]),
    )
    assert result.drops_by_rule == {
        "title_excludes": 0,
        "location_contains": 0,
        "location_missing": 0,
    }


def test_a_dead_pattern_is_reported() -> None:
    """The ``Arts and Music`` detector, on the guessed filter that caused it."""
    result = apply_filters(frontier_feed(), FRONTIER_GUESS)

    hits = result.hits_by_pattern["location_contains"]
    assert hits["Arts and Music"] == 0, "the string that never appears in the feed"
    assert hits["Makerspace"] == 1
    assert hits["Robotics"] == 1
    assert ("location_contains", "Arts and Music") in result.dead_patterns

    # ...and the guess keeps a tiny fraction of the feed, as recorded.
    assert result.kept_count == 5  # 2 matched + 3 kept by the missing-location rule
    assert result.drop_rate > 0.6


def test_the_first_matching_rule_owns_the_drop() -> None:
    """Documented order: excludes before keep-lists, one rule per dropped event."""
    off_site_and_held = event(
        "Hold - Denver satellite",
        location="2121 Larimer St, Denver, CO 80205, USA",
    )
    result = apply_filters([off_site_and_held], FRONTIER)

    assert result.dropped_count == 1
    assert result.dropped[0].rule is FilterRule.TITLE_EXCLUDES
    assert result.drops_by_rule["location_contains"] == 0


def test_summary_is_json_ready_for_health() -> None:
    import json

    result = apply_filters(frontier_feed(), FRONTIER)
    payload = result.summary()
    assert json.loads(json.dumps(payload)) == payload
    assert payload["space_id"] == "frontier-makerspace"
    assert payload["input"] == 16
    assert payload["kept"] == 10
    assert payload["by_rule"]["title_excludes"] == 3
    assert payload["kept_on_missing_location"] == 3
    assert payload["by_pattern"]["location_contains"]["Berlinhouse"] == 2


# --------------------------------------------------------------------------- logging


def test_every_drop_is_logged_with_the_rule_that_caused_it(caplog) -> None:
    """One grep for ``filter drop`` must answer "where did that event go?"."""
    caplog.set_level(logging.DEBUG, logger="pipeline.filters")
    result = apply_filters(frontier_feed(), FRONTIER)

    lines = [rec.getMessage() for rec in caplog.records if "filter drop" in rec.getMessage()]
    assert len(lines) == result.dropped_count == 6
    for item in result.dropped:
        assert any(item.title in line and item.rule.value in line for line in lines)


def test_a_filter_that_removes_almost_everything_warns(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="pipeline.filters")
    feed = [event(f"Event {n}", location="2121 Larimer St, Denver, CO 80205, USA") for n in range(10)]
    apply_filters(feed, Filters(location_contains=["995 Market"]), space_id="frontier-makerspace")

    warnings = [rec.getMessage() for rec in caplog.records if rec.levelno >= logging.WARNING]
    assert warnings, "a filter removing 100% of a feed must not be quiet"
    assert "silently" in warnings[0]


def test_no_filters_says_nothing_and_keeps_everything(caplog) -> None:
    caplog.set_level(logging.DEBUG, logger="pipeline.filters")
    feed = frontier_feed()
    result = apply_filters(feed, Filters())

    assert result.kept_count == len(feed)
    assert result.dropped == ()
    assert result.drops_by_rule == {}
    assert [rec for rec in caplog.records if rec.levelno >= logging.INFO] == []


def test_filters_may_be_omitted_entirely() -> None:
    feed = frontier_feed()
    result = apply_filters(feed)
    assert result.kept_count == len(feed)
    assert result.space_id == "frontier-makerspace"
    assert result.source_label == "luma-frontiertower"


# --------------------------------------------------------------------------- frontier


def test_the_real_frontier_filter_keeps_the_building_and_the_hidden_addresses() -> None:
    """The end-to-end case the registry actually ships.

    Keeps every event in the building, keeps all three hidden-address makerspace
    events, drops the three off-site venues and the three administrative
    placeholders — and drops nothing else.
    """
    result = apply_filters(frontier_feed(), FRONTIER)

    assert titles(result) == [
        "Frontier Tower Community Dinner",
        "Monthly Robotics Meetup",
        "MASS ARTS 5",
        "Otherminds Community Meet",
        "16th Floor Coffee Meetup",
        "Ethereum House Office Hours",
        "Makerspace Floor Welcome",
        "LASER FRYDAYS: Laser Cutter Training",
        "Frontier Makerspace All-Hands & Demo Night",
        "The SF Bay Area LeRobot Hackathon",
    ]
    assert result.kept_on_missing_location == 3
    assert {item.title for item in result.dropped} == {
        "Denver Satellite Demo",
        "Partner Mixer",
        "Community Potluck",
        "Hold - Floor 2 buildout",
        "TBA (speaker confirming)",
        "Placeholder for October summit",
    }


def test_the_hidden_address_events_are_exactly_what_the_strict_flag_would_lose() -> None:
    """Turning the flag off deletes three real makerspace events, silently."""
    strict = FRONTIER.model_copy(update={"location_allow_when_missing": False})
    lenient = apply_filters(frontier_feed(), FRONTIER)
    result = apply_filters(frontier_feed(), strict)

    lost = set(titles(lenient)) - set(titles(result))
    assert lost == {
        "LASER FRYDAYS: Laser Cutter Training",
        "Frontier Makerspace All-Hands & Demo Night",
        "The SF Bay Area LeRobot Hackathon",
    }
    assert result.drops_by_rule["location_missing"] == 3


# --------------------------------------------------------------------------- seam


def test_filter_normalization_carries_the_source_identity_and_spares_quarantine() -> None:
    """Quarantined events are already withheld; filtering them hides bugs."""
    kept = event("Open Shop", location="995 Market St, San Francisco")
    off_site = event("Denver Satellite Demo", location="2121 Larimer St, Denver, CO 80205, USA")
    held = event("x" * 250, location="995 Market St, San Francisco")

    normalization = Normalization(
        events=(kept, off_site),
        quarantined=(held,),
        space_id="frontier-makerspace",
        source_label="luma-frontiertower",
    )
    result = filter_normalization(normalization, FRONTIER)

    assert titles(result) == ["Open Shop"]
    assert result.space_id == "frontier-makerspace"
    assert result.source_label == "luma-frontiertower"
    assert normalization.quarantined[0].title == "x" * 250


def test_the_result_iterates_like_a_normalization() -> None:
    result = apply_filters(frontier_feed(), FRONTIER)
    assert len(result) == 10
    assert list(result)[0] is result[0]
    assert result.dropped_for(FilterRule.TITLE_EXCLUDES)[0].pattern == "Hold -"
