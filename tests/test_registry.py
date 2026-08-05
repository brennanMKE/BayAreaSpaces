"""Smoke tests for the source registry.

``sources.yaml`` is the only place adapters and feed URLs are named, and the
whole pipeline is downstream of it. These tests are a tripwire: they fail loudly
if the registry is accidentally truncated, if a source is dropped in a merge, or
if an unverified source is quietly promoted to ``verified: true`` without anyone
actually fetching it.

The counts below were confirmed against the 2026-08-05 source survey. When a
source is genuinely added or verified, update the constants **and** the
corresponding ``spaces/<id>.md`` research note in the same commit.
"""

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCES_YAML = REPO_ROOT / "sources.yaml"

# Confirmed 2026-08-05. See spaces/README.md for the coverage table.
EXPECTED_SPACES = 11
EXPECTED_SOURCES = 30
EXPECTED_VERIFIED = 25

# Adapters in use. Adding one means adding it here, to the header comment in
# sources.yaml, and to pipeline/adapters/.
KNOWN_ADAPTERS = {
    "ics",
    "gcal_ics",
    "tribe_rest",
    "jsonld",
    "nextdata",
    "embedded_json",
    "json",
    "rss",
    "bookwhen_html",
    "llm_html",
}


@pytest.fixture(scope="module")
def registry() -> dict:
    """The parsed registry. Must always load — this is the build gate."""
    with SOURCES_YAML.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@pytest.fixture(scope="module")
def sources(registry: dict) -> list[dict]:
    """Every source across every space, flattened."""
    return [src for space in registry["spaces"] for src in space.get("sources") or []]


def test_registry_file_exists():
    assert SOURCES_YAML.is_file(), f"registry missing at {SOURCES_YAML}"


def test_registry_parses(registry):
    assert isinstance(registry, dict)
    assert set(registry) >= {"defaults", "spaces"}


def test_defaults_block_is_complete(registry):
    defaults = registry["defaults"]
    for key in ("timezone", "user_agent", "rate_limit_seconds", "horizon_days"):
        assert key in defaults, f"defaults is missing {key!r}"
    assert defaults["timezone"] == "America/Los_Angeles"


def test_space_count(registry):
    spaces = registry["spaces"]
    assert isinstance(spaces, list)
    assert len(spaces) == EXPECTED_SPACES


def test_space_ids_are_unique_and_present(registry):
    ids = [space["id"] for space in registry["spaces"]]
    assert all(ids), "every space needs a non-empty id"
    assert len(set(ids)) == len(ids), f"duplicate space ids: {ids}"


def test_source_count(sources):
    assert len(sources) == EXPECTED_SOURCES


def test_verified_source_count(sources):
    verified = [src for src in sources if src.get("verified") is True]
    assert len(verified) == EXPECTED_VERIFIED, (
        "verified count changed. A source is only verified once it has actually "
        "been fetched and its event count recorded in spaces/<id>.md."
    )


def test_every_source_names_a_known_adapter(sources):
    for src in sources:
        adapter = src.get("adapter")
        assert adapter in KNOWN_ADAPTERS, f"unknown adapter {adapter!r} in {src}"


def test_every_source_has_a_target(sources):
    """Never guess a feed URL — but every source must at least name one."""
    for src in sources:
        has_target = bool(src.get("url")) or bool(src.get("calendar_id"))
        assert has_target, f"source has neither url nor calendar_id: {src}"
        if src["adapter"] == "gcal_ics":
            assert src.get("calendar_id"), f"gcal_ics needs calendar_id: {src}"


def test_verified_flag_is_explicit_and_boolean(sources):
    """Unverified sources must say so; absence is not the same as false."""
    for src in sources:
        assert isinstance(src.get("verified"), bool), (
            f"source must set verified: true|false explicitly: {src}"
        )
