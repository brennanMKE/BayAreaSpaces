"""Tests for ``out/health.json`` (issue 0017).

What is being defended:

**It is written on every run, blocked or not.** The blocked case is the
important one and it is the easy one to lose, because issue 0016's gate returns
early from :func:`~pipeline.cli.run_pipeline` before the emit step. A night that
published nothing is the night someone reads this file.

**The three-night condition is readable, not reconstructible.** CLAUDE.md
dispatches the OpenCode repair pass when a source shows 0 for three consecutive
nights, with ``raw/`` from those nights as the diff material. If a consumer has
to join three nightly documents to work that out, it will not happen at 03:15
and it will not happen reliably by hand either. ``consecutive_zero_nights``,
``repair_ready`` and ``repair.candidates`` are that condition, already answered.

**"Did the source change or did we?" in ten seconds.** ``raw_path`` on every
source, and the raw paths of the zero nights on every repair candidate. That
question is the entire reason ``raw/`` is retained.

**A dry run does not touch the live file.** Same rule as the calendar and the
store: the diagnostic must not damage the thing under diagnosis.

No test here touches the network — every request goes through
``httpx.MockTransport``, and the LM Studio probe is injected, for the reasons
``tests/test_cli.py`` sets out at length.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import pytest

from pipeline.cli import STAGING_DIRNAME, HealthDecision, SourceRecord, run_pipeline
from pipeline.config import CONTACT_ENV_VAR, load_registry
from pipeline.health_json import (
    HEALTH_FILENAME,
    REPAIR_ZERO_NIGHTS,
    SCHEMA,
    build_health_document,
    write_health_json,
    zero_nights,
)
from pipeline.store import open_store

from tests.test_cli import (
    FIXED_NOW,
    TEST_CONTACT,
    FakeClock,
    calendar_transport,
    noop_sleep,
    offline_llm,
)

#: One space, two ``ics`` sources. Enough to prove every source is represented
#: without a fixture that takes a second to run.
SPACE = "hacker-dojo"

#: A syntactically perfect calendar with nothing in it. This is what a drifted
#: adapter looks like from the outside: HTTP 200, right content type, zero
#: events, no error anywhere.
EMPTY_ICS = b"""BEGIN:VCALENDAR\r
VERSION:2.0\r
PRODID:-//test//maker-calendar//EN\r
X-WR-CALNAME:Empty Feed\r
END:VCALENDAR\r
"""


# --------------------------------------------------------------------------- harness


@pytest.fixture
def env() -> dict[str, str]:
    return {CONTACT_ENV_VAR: TEST_CONTACT}


@pytest.fixture
def registry(env: dict[str, str]):
    return load_registry(env=env)


def run(tmp_path: Path, registry, **kwargs):
    """A single-space run with every seam pointed somewhere harmless."""
    kwargs.setdefault("transport", calendar_transport())
    kwargs.setdefault("now", FIXED_NOW)
    return run_pipeline(
        registry,
        space_id=kwargs.pop("space_id", SPACE),
        out_dir=tmp_path / "out",
        raw_dir=tmp_path / "raw",
        db_path=tmp_path / "events.sqlite",
        sleep=noop_sleep,
        clock=FakeClock(),
        llm_probe=offline_llm,
        **kwargs,
    )


def read(path: Path | None) -> dict:
    assert path is not None, "health.json was not written"
    return json.loads(Path(path).read_text(encoding="utf-8"))


def source_in(document: dict, label: str) -> dict:
    for entry in document["sources"]:
        if entry["label"] == label:
            return entry
    raise AssertionError(f"{label} is missing from health.json")


# --------------------------------------------------------------------------- the file


def test_a_successful_run_writes_a_parseable_health_json(tmp_path: Path, registry):
    report = run(tmp_path, registry)

    path = tmp_path / "out" / STAGING_DIRNAME / HEALTH_FILENAME
    assert report.health_json_path == path
    assert report.health_json_error is None

    document = read(path)
    assert document["schema"] == SCHEMA
    assert document["run"]["published"] is True
    assert document["totals"]["events"] == report.event_count


def test_every_source_in_the_run_is_represented(tmp_path: Path, registry):
    """Two sources ran; two sources appear. A source that vanishes from the

    diagnostics is indistinguishable from a source nobody registered."""
    report = run(tmp_path, registry)
    document = read(report.health_json_path)

    assert len(document["sources"]) == len(report.records)
    assert {entry["key"] for entry in document["sources"]} == {
        record.key for record in report.records
    }


def test_the_document_round_trips_and_carries_its_schema_version(
    tmp_path: Path, registry
):
    """The website project may pin this. A shape change must be detectable."""
    report = run(tmp_path, registry)
    raw = Path(report.health_json_path).read_text(encoding="utf-8")

    document = json.loads(raw)
    assert document["schema"] == SCHEMA
    assert document["pipeline_version"]
    assert json.loads(json.dumps(document)) == document


def test_a_blocked_run_still_writes_health_json(tmp_path: Path, registry, monkeypatch):
    """**The important one.** Nothing was published, so this is all that is left.

    Issue 0016's gate returns early, before the emit step, and the early return
    is exactly where a "write it at the end" implementation loses the file.
    """
    monkeypatch.setattr(
        "pipeline.cli.evaluate_health",
        lambda records, **kwargs: HealthDecision(
            blocked=True, reasons=("global count dropped 62%",), implemented=True
        ),
    )
    report = run(tmp_path, registry)

    assert report.published is False
    assert not (tmp_path / "out" / STAGING_DIRNAME / "calendar.ics").exists()

    document = read(report.health_json_path)
    assert document["gates"]["blocked"] is True
    assert document["gates"]["reasons"] == ["global count dropped 62%"]
    assert document["run"]["published"] is False
    assert "global count dropped 62%" in document["run"]["publish_skipped_reason"]
    assert len(document["sources"]) == len(report.records)


# --------------------------------------------------------------------------- per source


def test_a_source_record_carries_raw_path_counts_and_its_gate_verdict(
    tmp_path: Path, registry
):
    """The ten-second question: what did it return, what did it return last

    night, what did the gates make of it, and which file do I diff?"""
    run(tmp_path, registry)  # night one, so night two has a baseline
    report = run(tmp_path, registry)
    entry = source_in(read(report.health_json_path), "meetup-ical")

    assert entry["raw_path"], "raw/ is the answer to 'did the source change?'"
    assert Path(entry["raw_path"]).exists()
    assert entry["horizon_count"] == 1
    assert entry["previous_count"] == 1
    assert entry["gate_outcome"] == "ok"
    assert entry["gate"]["key"] == "hacker-dojo:meetup-ical"
    assert entry["gate"]["horizon_count"] == 1

    # The transport-level facts, straight off SourceRecord.
    assert entry["http_status"] == 200
    assert entry["content_type"] == "text/calendar"
    assert entry["bytes"] > 0
    assert entry["last_change"] == "2026-08-04T12:00:00+00:00"
    assert entry["stale_days"] is not None


def test_filter_and_dedupe_counts_appear(tmp_path: Path, registry):
    """Composed from ``FilterResult.summary`` and ``DedupeResult.summary``.

    Not recomputed here: the module that owns the number owns the accounting.
    """
    report = run(tmp_path, registry)
    document = read(report.health_json_path)

    entry = source_in(document, "meetup-ical")
    assert entry["filters"]["kept"] == 1
    assert entry["filters"]["dropped"] == 0
    assert "by_rule" in entry["filters"]
    assert "dead_patterns" in entry["filters"]

    dedupe = document["run"]["dedupe"]
    assert dedupe["merged"] == report.merge_count
    assert dedupe["input"] == 2
    assert "near_miss_count" in dedupe


def test_a_filter_that_ate_the_feed_is_visible_by_rule(tmp_path: Path, registry):
    """The Frontier bug: 9 of 262 kept, a green run, 97% of a building missing."""
    space = registry.space(SPACE)
    source = space.sources[0]
    object.__setattr__(source.filters, "title_excludes", ("Workshop",))

    report = run(tmp_path, registry)
    entry = source_in(read(report.health_json_path), source.label or source.adapter)

    assert entry["event_count"] == 0
    assert entry["filters"]["dropped"] == 1
    assert entry["filters"]["by_rule"]["title_excludes"] == 1
    assert entry["filters"]["by_pattern"]["title_excludes"]["Workshop"] == 1


# --------------------------------------------------------------------------- the streak


def test_three_zero_nights_are_directly_readable(tmp_path: Path, registry):
    """The condition the repair workflow dispatches on, already answered.

    Three nights of a valid, empty calendar — HTTP 200, right content type, no
    error. The count is on the source record and the space shows up in
    ``repair.candidates`` with the raw bodies to diff.
    """
    nights = [FIXED_NOW + dt.timedelta(days=offset) for offset in range(3)]
    documents = []
    for night in nights:
        report = run(
            tmp_path,
            registry,
            now=night,
            transport=calendar_transport(EMPTY_ICS),
        )
        documents.append(read(report.health_json_path))

    counts = [source_in(doc, "meetup-ical")["consecutive_zero_nights"] for doc in documents]
    assert counts == [1, 2, 3]

    first, second, third = documents
    assert source_in(first, "meetup-ical")["repair_ready"] is False
    assert source_in(second, "meetup-ical")["repair_ready"] is False
    assert source_in(third, "meetup-ical")["repair_ready"] is True

    assert third["repair"]["zero_night_threshold"] == REPAIR_ZERO_NIGHTS
    assert third["repair"]["candidate_count"] >= 1
    candidate = next(
        item for item in third["repair"]["candidates"] if item["label"] == "meetup-ical"
    )
    assert candidate["space_id"] == SPACE
    assert candidate["consecutive_zero_nights"] == 3
    assert candidate["raw_paths"], "the repair pass diffs raw/; give it the paths"
    assert all(Path(raw).exists() for raw in candidate["raw_paths"])

    # Each night is listed, newest first, with its own evidence.
    entry = source_in(third, "meetup-ical")
    assert len(entry["zero_nights"]) == 3
    assert entry["zero_nights"][0]["horizon_count"] == 0


def test_a_non_zero_night_ends_the_streak(tmp_path: Path, registry):
    """Two empty nights then a real one. The streak is over, not merely paused."""
    for offset in range(2):
        run(
            tmp_path,
            registry,
            now=FIXED_NOW + dt.timedelta(days=offset),
            transport=calendar_transport(EMPTY_ICS),
        )
    report = run(tmp_path, registry, now=FIXED_NOW + dt.timedelta(days=2))

    entry = source_in(read(report.health_json_path), "meetup-ical")
    assert entry["consecutive_zero_nights"] == 0
    assert entry["repair_ready"] is False
    assert entry["zero_nights"] == []


def test_a_skipped_source_never_becomes_a_repair_candidate(tmp_path: Path, registry):
    """Eight adapters are still issues 0019-0028 and return 0 every night.

    Counting those as drift would put a permanent list of false candidates in
    front of whoever reads this file, which is how an alert stops being read.
    """
    for offset in range(4):
        report = run(
            tmp_path,
            registry,
            space_id=None,
            now=FIXED_NOW + dt.timedelta(days=offset),
        )
    document = read(report.health_json_path)

    pending = [
        entry
        for entry in document["sources"]
        if entry["skipped_because"] == "adapter_not_implemented"
    ]
    assert pending, "the registry still names unimplemented adapters"
    assert all(entry["consecutive_zero_nights"] == 0 for entry in pending)
    assert all(entry["repair_ready"] is False for entry in pending)


def test_a_304_night_is_not_a_zero_night():
    """A 304 is the server saying the source is current. The opposite of drift.

    ``horizon_count`` is 0 on a 304 because there is no body to count, not
    because the feed emptied — issue 0014 made that distinction and this must
    not undo it by treating the healthiest possible response as evidence of a
    broken adapter.
    """
    record = SourceRecord(
        space_id="s",
        label="l",
        adapter="ics",
        status="not_modified",
        reused_unchanged=True,
        carry_forward_count=12,
    )
    assert zero_nights(record, None) == []


def test_the_run_level_answers_what_it_promises(tmp_path: Path, registry):
    report = run(tmp_path, registry)
    document = read(report.health_json_path)

    assert document["run"]["started_at"] == report.started_at.isoformat()
    assert document["run"]["finished_at"] is not None
    assert document["totals"]["by_space"] == {SPACE: report.event_count}
    assert document["totals"]["gate_outcomes"]["sources"] == len(report.records)
    assert document["llm"]["ran"] is False
    assert "LM Studio is not answering" in document["llm"]["skipped_reason"]
    assert document["run"]["emit"]["events"] == report.event_count


# --------------------------------------------------------------------------- writing


def test_a_dry_run_stages_and_leaves_the_live_file_alone(tmp_path: Path, registry):
    live = tmp_path / "out" / HEALTH_FILENAME
    live.parent.mkdir(parents=True)
    live.write_text('{"schema": 1, "from": "last night"}', encoding="utf-8")

    report = run(tmp_path, registry, dry_run=True)

    staged = tmp_path / "out" / STAGING_DIRNAME / HEALTH_FILENAME
    assert report.health_json_path == staged
    assert staged.exists()
    assert read(staged)["run"]["dry_run"] is True
    assert json.loads(live.read_text(encoding="utf-8")) == {
        "schema": 1,
        "from": "last night",
    }


def test_a_full_run_writes_the_live_file(tmp_path: Path, registry):
    """``--space`` and ``--dry-run`` stage; a real full run writes ``out/``."""
    report = run(tmp_path, registry, space_id=None)
    assert report.health_json_path == tmp_path / "out" / HEALTH_FILENAME


def test_a_failed_write_leaves_no_partial_file(tmp_path: Path, registry, monkeypatch):
    """Atomic, like the ICS emit: the old document or the new one, never a prefix."""
    report = run(tmp_path, registry)
    live = tmp_path / "live"
    live.mkdir()
    target = live / HEALTH_FILENAME
    target.write_bytes(b'{"schema": 1, "from": "last night"}')

    def boom(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)

    with pytest.raises(OSError, match="disk full"):
        write_health_json(report, out_dir=live)

    assert target.read_bytes() == b'{"schema": 1, "from": "last night"}'
    assert list(live.iterdir()) == [target], "no temp file may be left behind"


def test_a_health_json_failure_does_not_fail_the_run(
    tmp_path: Path, registry, monkeypatch
):
    """The calendar is already published. Losing diagnostics is the smaller loss."""
    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("pipeline.cli.write_health_json", boom)
    report = run(tmp_path, registry)

    assert report.exit_code == 0
    assert report.published is True
    assert report.health_json_path is None
    assert "disk full" in (report.health_json_error or "")


def test_the_document_is_built_without_touching_the_filesystem(
    tmp_path: Path, registry
):
    """``build_health_document`` is pure, so a consumer can render it directly."""
    report = run(tmp_path, registry)
    with open_store(tmp_path / "events.sqlite") as store:
        document = build_health_document(report, store=store, path="/nowhere/health.json")

    assert document["path"] == "/nowhere/health.json"
    assert not (tmp_path / "nowhere").exists()
    assert json.loads(json.dumps(document, default=str))["schema"] == SCHEMA
