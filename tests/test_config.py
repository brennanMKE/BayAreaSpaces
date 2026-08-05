"""Tests for the typed registry loader (issue 0005).

Two things are being defended here, and they are different jobs.

The first is the **contact**. ``$MAKER_CALENDAR_CONTACT`` is interpolated into
the User-Agent of every request the nightly job makes, and the robots.txt
position in ``CLAUDE.md`` rests entirely on a site operator being able to reach
us. Unset, empty and ``example.com`` must each stop the run before a single
request goes out — there is no degraded mode.

The second is **fail-loud schema validation**. An unknown adapter name, filter
key or health key must raise. The failure this project keeps trying to
eliminate is the typo that silently does nothing at 03:15 and looks exactly
like a source going quietly dead.
"""

from datetime import date
from pathlib import Path

import pytest
import yaml

from pipeline.config import (
    CONTACT_ENV_VAR,
    KNOWN_ADAPTERS,
    SOURCES_YAML,
    ConfigError,
    ContactError,
    Registry,
    RegistryError,
    load_registry,
    resolve_contact,
)

# A contact that is well-formed and obviously not the .env.example placeholder.
TEST_CONTACT = "https://maker-calendar.test/about"

# Confirmed against the real registry, 2026-08-05. Mirrors tests/test_registry.py
# so registry damage fails in both the raw-YAML and the typed-loader tests.
EXPECTED_SPACES = 11
EXPECTED_SOURCES = 30
EXPECTED_VERIFIED = 25
EXPECTED_DISABLED = 4


@pytest.fixture
def env() -> dict[str, str]:
    """A minimal environment carrying a valid contact."""
    return {CONTACT_ENV_VAR: TEST_CONTACT}


@pytest.fixture
def registry(env):
    """The real ``sources.yaml``, loaded through the typed loader."""
    return load_registry(SOURCES_YAML, env=env)


def write_registry(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "sources.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def minimal(**space_overrides) -> dict:
    """A one-space registry, as small as the schema allows."""
    space = {
        "id": "test-space",
        "name": "Test Space",
        "city": "Oakland",
        "region": "east-bay",
        "url": "https://example.org/",
        "sources": [
            {
                "adapter": "ics",
                "url": "https://example.org/events.ics",
                "label": "ics",
                "trust": 100,
                "verified": True,
            }
        ],
    }
    space.update(space_overrides)
    return {
        "defaults": {
            "timezone": "America/Los_Angeles",
            "user_agent": "bayarea-maker-calendar/0.1 (+${MAKER_CALENDAR_CONTACT})",
            "rate_limit_seconds": 2,
            "horizon_days": 120,
        },
        "spaces": [space],
    }


# --------------------------------------------------------------- the real registry


def test_real_registry_loads(registry):
    """The build gate: the shipped registry must always load cleanly."""
    assert isinstance(registry, Registry)
    assert len(registry.spaces) == EXPECTED_SPACES
    assert len(registry.all_sources) == EXPECTED_SOURCES


def test_real_registry_verified_count(registry):
    verified = [ref for ref in registry.all_sources if ref.source.verified]
    assert len(verified) == EXPECTED_VERIFIED


def test_real_registry_adapters_are_all_known(registry):
    for ref in registry.all_sources:
        assert ref.source.adapter in KNOWN_ADAPTERS


def test_defaults_are_parsed(registry):
    assert registry.timezone == "America/Los_Angeles"
    assert registry.horizon_days == 120
    assert registry.defaults.rate_limit_seconds == 2


def test_user_agent_has_the_contact_expanded(registry):
    assert TEST_CONTACT in registry.user_agent
    assert "${" not in registry.user_agent
    assert registry.user_agent.startswith("bayarea-maker-calendar/")


def test_gcal_ics_sources_carry_a_calendar_id(registry):
    gcal = [ref.source for ref in registry.all_sources if ref.source.adapter == "gcal_ics"]
    assert gcal, "registry should still contain gcal_ics sources"
    for source in gcal:
        assert source.calendar_id
        assert source.url is None
        assert source.target == source.calendar_id


def test_filters_are_typed(registry):
    """Frontier Tower's location filter is the one that must not silently drop."""
    frontier = registry.space("frontier-makerspace")
    luma = next(src for src in frontier.sources if src.label == "luma-frontiertower")
    assert "Frontier Tower" in luma.filters.location_contains
    assert luma.filters.location_allow_when_missing is True
    assert luma.filters.title_excludes == ["Hold -", "TBA (", "Placeholder"]


def test_health_overrides_are_typed(registry):
    dojo = registry.space("hacker-dojo")
    meetup = next(src for src in dojo.sources if src.label == "meetup-ical")
    assert meetup.health.ignore_count_drop is True

    noisebridge = registry.space("noisebridge")
    stale = next(src for src in noisebridge.sources if src.label == "noisebridge-today")
    assert stale.health.max_stale_days == 180

    # Health also lives at space level.
    assert registry.space("the-box-shop").health.allow_zero is True


def test_spaceapi_checked_parses_as_a_date(registry):
    spaceapi = registry.space("sudo-room").spaceapi
    assert spaceapi is not None
    assert spaceapi.status == "dead"
    assert spaceapi.checked == date(2026, 8, 5)
    assert isinstance(spaceapi.checked, date)


def test_address_override_is_preserved(registry):
    assert registry.space("the-crucible").address_override == "1260 7th St, Oakland, CA 94607"


# ------------------------------------------------------------------ contact validation


def test_missing_contact_raises():
    with pytest.raises(ContactError) as excinfo:
        load_registry(SOURCES_YAML, env={})
    assert CONTACT_ENV_VAR in str(excinfo.value)


def test_empty_contact_raises():
    with pytest.raises(ContactError):
        load_registry(SOURCES_YAML, env={CONTACT_ENV_VAR: ""})


def test_whitespace_only_contact_raises():
    with pytest.raises(ContactError):
        load_registry(SOURCES_YAML, env={CONTACT_ENV_VAR: "   "})


def test_example_com_contact_raises():
    with pytest.raises(ContactError) as excinfo:
        load_registry(SOURCES_YAML, env={CONTACT_ENV_VAR: "https://example.com/maker-calendar"})
    assert "placeholder" in str(excinfo.value)


def test_example_com_contact_raises_regardless_of_case():
    with pytest.raises(ContactError):
        resolve_contact({CONTACT_ENV_VAR: "https://WWW.EXAMPLE.COM/bot"})


def test_non_url_contact_raises():
    """The contact is an about-page URL, not an email — see CLAUDE.md."""
    with pytest.raises(ContactError):
        resolve_contact({CONTACT_ENV_VAR: "someone@somewhere.org"})


def test_contact_with_whitespace_raises():
    with pytest.raises(ContactError):
        resolve_contact({CONTACT_ENV_VAR: "https://maker.test/a b"})


def test_contact_errors_are_config_errors():
    assert issubclass(ContactError, ConfigError)
    assert issubclass(RegistryError, ConfigError)


def test_resolve_contact_reads_the_process_environment(monkeypatch):
    monkeypatch.setenv(CONTACT_ENV_VAR, TEST_CONTACT)
    assert resolve_contact() == TEST_CONTACT
    monkeypatch.delenv(CONTACT_ENV_VAR)
    with pytest.raises(ContactError):
        resolve_contact()


def test_unknown_placeholder_raises(tmp_path, env):
    data = minimal()
    data["defaults"]["user_agent"] = "bot/0.1 (+${MAKER_CALENDER_CONTACT})"  # typo
    path = write_registry(tmp_path, data)
    with pytest.raises(RegistryError) as excinfo:
        load_registry(path, env=env)
    assert "MAKER_CALENDER_CONTACT" in str(excinfo.value)


# ------------------------------------------------------------- fail-loud schema errors


def test_unknown_adapter_raises(tmp_path, env):
    data = minimal()
    data["spaces"][0]["sources"][0]["adapter"] = "icals"  # plausible typo
    path = write_registry(tmp_path, data)
    with pytest.raises(RegistryError) as excinfo:
        load_registry(path, env=env)
    assert "adapter" in str(excinfo.value)


def test_unknown_filter_key_raises(tmp_path, env):
    data = minimal()
    data["spaces"][0]["sources"][0]["filters"] = {"location_contain": ["Oakland"]}
    path = write_registry(tmp_path, data)
    with pytest.raises(RegistryError) as excinfo:
        load_registry(path, env=env)
    assert "location_contain" in str(excinfo.value)


def test_unknown_health_key_raises(tmp_path, env):
    data = minimal()
    data["spaces"][0]["sources"][0]["health"] = {"allow_zeros": True}
    path = write_registry(tmp_path, data)
    with pytest.raises(RegistryError) as excinfo:
        load_registry(path, env=env)
    assert "allow_zeros" in str(excinfo.value)


def test_unknown_source_key_raises(tmp_path, env):
    data = minimal()
    data["spaces"][0]["sources"][0]["urls"] = "https://example.org/events.ics"
    path = write_registry(tmp_path, data)
    with pytest.raises(RegistryError):
        load_registry(path, env=env)


def test_unknown_space_key_raises(tmp_path, env):
    data = minimal(rate_limit_second=10)
    path = write_registry(tmp_path, data)
    with pytest.raises(RegistryError):
        load_registry(path, env=env)


def test_gcal_ics_without_calendar_id_raises(tmp_path, env):
    data = minimal()
    data["spaces"][0]["sources"][0] = {
        "adapter": "gcal_ics",
        "url": "https://calendar.google.com/whatever/basic.ics",
        "verified": True,
    }
    path = write_registry(tmp_path, data)
    with pytest.raises(RegistryError) as excinfo:
        load_registry(path, env=env)
    assert "calendar_id" in str(excinfo.value)


def test_embedded_json_without_script_id_raises(tmp_path, env):
    data = minimal()
    data["spaces"][0]["sources"][0]["adapter"] = "embedded_json"
    data["spaces"][0]["sources"][0]["url"] = "https://example.org/courses/"
    path = write_registry(tmp_path, data)
    with pytest.raises(RegistryError) as excinfo:
        load_registry(path, env=env)
    assert "script_id" in str(excinfo.value)


def test_duplicate_space_ids_raise(tmp_path, env):
    data = minimal()
    data["spaces"].append(dict(data["spaces"][0]))
    path = write_registry(tmp_path, data)
    with pytest.raises(RegistryError) as excinfo:
        load_registry(path, env=env)
    assert "duplicate space ids" in str(excinfo.value)


def test_malformed_yaml_raises(tmp_path, env):
    path = tmp_path / "sources.yaml"
    path.write_text("defaults: [unclosed\n", encoding="utf-8")
    with pytest.raises(RegistryError):
        load_registry(path, env=env)


def test_missing_file_raises(tmp_path, env):
    with pytest.raises(RegistryError):
        load_registry(tmp_path / "nope.yaml", env=env)


# -------------------------------------------------------------------- the run set


def test_disabled_sources_load_but_are_excluded_from_the_run_set(registry):
    assert len(registry.skipped_sources) == EXPECTED_DISABLED
    assert len(registry.enabled_sources) == EXPECTED_SOURCES - EXPECTED_DISABLED
    assert len(registry.enabled_sources) + len(registry.skipped_sources) == len(
        registry.all_sources
    )

    skipped = {(ref.space.id, ref.source.label) for ref in registry.skipped_sources}
    assert ("noisebridge", "noisebridge-today") in skipped
    assert ("the-crucible", "public-events-seed") in skipped
    assert ("the-box-shop", "eventbrite-organizer") in skipped
    assert ("maker-nexus", "classes-week-view-embed") in skipped

    # And the same distinction is visible per space.
    noisebridge = registry.space("noisebridge")
    labels = [src.label for src in noisebridge.sources]
    assert "noisebridge-today" in labels, "disabled sources still load"
    assert "noisebridge-today" not in [src.label for src in noisebridge.enabled_sources]
    assert "noisebridge-today" in [src.label for src in noisebridge.skipped_sources]


def test_iter_enabled_matches_the_run_set(registry):
    assert list(registry.iter_enabled()) == registry.enabled_sources
    assert all(ref.source.enabled for ref in registry.iter_enabled())


def test_sources_default_to_enabled(registry):
    ace = registry.space("ace-makerspace")
    assert [src.enabled for src in ace.sources] == [True, True, True]
    assert ace.enabled_sources == ace.sources
    assert ace.skipped_sources == []


# ------------------------------------------------------------------- rate limiting


def test_ace_rate_limit_overrides_the_default(registry):
    """Ace's robots.txt sets Crawl-delay: 10, above the global default of 2."""
    assert registry.space("ace-makerspace").rate_limit_seconds == 10
    assert registry.space("ace-makerspace").effective_rate_limit_seconds == 10


def test_other_spaces_inherit_the_default_rate_limit(registry):
    sudo = registry.space("sudo-room")
    assert sudo.rate_limit_seconds is None, "no per-space override in the registry"
    assert sudo.effective_rate_limit_seconds == 2
    assert registry.space("the-crucible").effective_rate_limit_seconds == 2


def test_only_ace_overrides_the_rate_limit(registry):
    overridden = {
        space.id for space in registry.spaces if space.rate_limit_seconds is not None
    }
    assert overridden == {"ace-makerspace"}


# ------------------------------------------------------------------- empty and TODO


def test_lower_48_has_no_sources_and_still_loads(registry):
    lower48 = registry.space("lower-48")
    assert lower48.sources == []
    assert lower48.enabled_sources == []
    assert lower48.health.allow_zero is True, "an empty space must not alert nightly"


def test_todo_url_is_accepted_and_detectable(registry):
    """Issue 0002: the Bookwhen token is missing, so the URL is literally TODO."""
    todo = registry.todo_sources
    assert [(ref.space.id, ref.source.label) for ref in todo] == [
        ("sequoia-fabrica", "bookwhen-public")
    ]
    source = todo[0].source
    assert source.url == "TODO"
    assert source.is_todo is True
    assert source.verified is False, "a TODO url can never be a verified source"

    # Everything else must be a real target the fetch layer can use.
    for ref in registry.all_sources:
        if ref.source.is_todo:
            continue
        assert ref.source.target, f"{ref.space.id}/{ref.source.label} has no target"


def test_real_urls_are_not_flagged_as_todo(registry):
    ace = registry.space("ace-makerspace")
    assert all(src.is_todo is False for src in ace.sources)
