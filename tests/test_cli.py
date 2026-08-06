"""Tests for the CLI entry point (issue 0012).

**No test here touches the network.** Every request goes through
``httpx.MockTransport``, including the ``robots.txt`` lookups the fetch layer
makes on its own. Issue 0001 (the bot about page must resolve before the first
fetch) is still open and ``$MAKER_CALENDAR_CONTACT`` is unset on this machine,
so a live request would go out under a User-Agent pointing at a page that does
not exist — the exact "bot performing accountability" failure CLAUDE.md names.
The LM Studio probe is injected for the same reason: a real one would try to
open a socket to ``localhost:1234``.

What is being defended:

**A run today produces a calendar.** Six of the ten adapter names in
``sources.yaml`` are still issues 0021-0025 and 0028. Meeting one must skip with
its issue number, not end the run — otherwise Phase 1 delivers nothing until
Phase 2 is finished.

**Nothing publishes by accident.** ``--dry-run`` writes to ``out/.staging/`` and
``out/calendar.ics`` is not touched. Neither is a single-space run allowed to
replace the merged calendar with one space's worth of events.

**The environment is loaded, never overridden.** launchd does not read the shell
profile, so ``.env`` must be read explicitly (issue 0031) — but a value already
in the real environment always wins, or ``FOO=bar python -m pipeline run`` lies.

**A missing contact is a message, not a traceback.** It is the most common
startup failure and it is always a one-line fix.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from pipeline.config import CONTACT_ENV_VAR, KNOWN_ADAPTERS, load_registry
from pipeline.cli import (
    ADAPTERS,
    EXIT_CONFIG,
    EXIT_HEALTH_BLOCKED,
    EXIT_OK,
    STAGING_DIRNAME,
    HealthDecision,
    LmStudioStatus,
    SourceRecord,
    evaluate_health,
    implemented_adapters,
    is_runnable,
    iter_sources,
    load_dotenv,
    main,
    parse_env_file,
    run_pipeline,
)

TEST_CONTACT = "https://maker-calendar.test/about"
FIXED_NOW = dt.datetime(2026, 8, 5, 3, 15, 0, tzinfo=dt.timezone.utc)

# Confirmed against the 2026-08-05 survey; mirrors tests/test_registry.py.
EXPECTED_SPACES = 11
EXPECTED_SOURCES = 30
EXPECTED_ENABLED = 26
EXPECTED_DISABLED = 4
EXPECTED_TODO = 1
#: enabled, not TODO, and naming an adapter that exists today (ics, gcal_ics,
#: tribe_rest, jsonld). Issue 0019 added Ace's REST feed; issue 0020 added Ace's
#: JSON-LD calendar page and The Box Shop's two-step Squarespace source.
EXPECTED_RUNNABLE = 16

ICS_BODY = b"""BEGIN:VCALENDAR\r
VERSION:2.0\r
PRODID:-//test//maker-calendar//EN\r
X-WR-CALNAME:Test Feed\r
X-WR-TIMEZONE:America/Los_Angeles\r
BEGIN:VEVENT\r
UID:evt-1@test\r
DTSTAMP:20260804T120000Z\r
LAST-MODIFIED:20260804T120000Z\r
DTSTART:20260810T180000Z\r
DTEND:20260810T200000Z\r
SUMMARY:Open Shop Night\r
LOCATION:1234 Test Ave, Oakland, CA\r
DESCRIPTION:Come make something.\r
URL:https://example.test/events/1\r
END:VEVENT\r
END:VCALENDAR\r
"""


# --------------------------------------------------------------------------- harness


def noop_sleep(seconds: float) -> None:
    """Rate limiting is 2 s per host and 10 s for Ace. Not in a test suite."""


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def feed_body(url: httpx.URL) -> bytes:
    """The fixture calendar, with a UID **and a title** derived from the feed.

    Two different feeds ship two different events, which is the normal case.
    The title has to vary as well as the UID since issue 0015: dedupe matches
    on start time plus fuzzy title, so a fixture where thirteen sources all
    served one identically-named event at one address would legitimately
    collapse to a single record and every count in this file would stop
    measuring what it says it measures. The digest keeps the titles far apart
    under ``token_set_ratio`` without making them unreadable.

    The duplicate cases are tested separately by pinning ``body``.
    """
    slug = "".join(char if char.isalnum() else "-" for char in str(url))
    digest = hashlib.sha1(str(url).encode()).hexdigest()[:8]
    return ICS_BODY.replace(
        b"UID:evt-1@test", f"UID:evt-{slug}@test".encode()
    ).replace(b"SUMMARY:Open Shop Night", f"SUMMARY:Workshop {digest}".encode())


def tribe_body(url: httpx.URL) -> bytes:
    """One TEC event, JSON, on a single page — the ``tribe_rest`` counterpart.

    Ace's REST feed is the only ``tribe_rest`` source in the registry (issue
    0019) and it does not speak iCalendar, so the calendar transport would hand
    it a ``text/calendar`` body and it would correctly report a drifted source.
    That would leave the run's counts measuring an adapter failure rather than
    an adapter. Same shape as :func:`feed_body`: one event, inside the horizon,
    with a title far enough from the others that dedupe leaves it alone.

    No ``next_rest_url``, so pagination ends on page 1 — the walk itself is
    tested in ``tests/test_adapter_tribe_rest.py``, not here.
    """
    digest = hashlib.sha1(str(url).encode()).hexdigest()[:8]
    payload = {
        "events": [
            {
                "id": 40100,
                "global_id": f"tribe-{digest}",
                "status": "publish",
                "url": "https://example.test/event/tribe-1",
                "title": f"Laser Night {digest}",
                "description": "Come make something.",
                "all_day": False,
                "start_date": "2026-08-11 11:00:00",
                "end_date": "2026-08-11 13:00:00",
                "utc_start_date": "2026-08-11 18:00:00",
                "utc_end_date": "2026-08-11 20:00:00",
                "timezone": "America/Los_Angeles",
                "cost": "&#036;20.00",
                "is_virtual": False,
                "ticketed": True,
                "venue": {
                    "venue": "Test Venue",
                    "address": "1234 Test Ave",
                    "city": "Oakland",
                    "province": "CA",
                },
                "organizer": [{"organizer": "Team Test"}],
                "categories": [{"name": "Laser", "slug": "laser-events"}],
            }
        ],
        "total": 1,
        "total_pages": 1,
    }
    return json.dumps(payload).encode("utf-8")


def jsonld_body(url: httpx.URL) -> bytes:
    """One ``schema.org/Event`` in an ld+json block — the ``jsonld`` counterpart.

    Same shape as :func:`feed_body`: one event, inside the horizon, with a title
    far enough from the others that dedupe leaves it alone. Ace's ``/calendar/``
    and The Box Shop's per-event pages both answer with this.
    """
    digest = hashlib.sha1(str(url).encode()).hexdigest()[:8]
    return (
        "<!DOCTYPE html><html><head>"
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"WebSite","name":"Test"}'
        "</script>"
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"Event",'
        f'"name":"Salon {digest}",'
        f'"url":"{url}",'
        '"startDate":"2026-08-12T18:00:00-07:00",'
        '"endDate":"2026-08-12T21:00:00-07:00",'
        '"description":"Come make something.",'
        '"location":{"@type":"Place","name":"Test Venue",'
        '"address":{"streetAddress":"1234 Test Ave","addressLocality":"Oakland",'
        '"addressRegion":"CA"}}}'
        "</script></head><body></body></html>"
    ).encode("utf-8")


def jsonld_seed_body(url: httpx.URL) -> bytes:
    """The Box Shop's ``?format=rss`` seed list: one item, and **no date**.

    ``pubDate`` is the post date, which is exactly why the adapter follows the
    link rather than reading a date here (issue 0020).
    """
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel><title>Events</title>'
        f"<link>{url}</link>"
        "<item><title>Salon</title>"
        f"<link>https://{url.host}/events/salon</link>"
        "<pubDate>Mon, 29 Jun 2026 19:04:11 +0000</pubDate>"
        "</item></channel></rss>"
    ).encode("utf-8")


def calendar_transport(body: bytes | None = None) -> httpx.MockTransport:
    """``robots.txt`` 404, TEC REST as JSON, JSON-LD pages as HTML, rest as ICS.

    Every adapter in the dispatch table needs a body it can actually parse, or
    the run's counts stop measuring the run and start measuring an adapter
    correctly rejecting a payload it was never pointed at.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        url = request.url
        if url.path == "/robots.txt":
            return httpx.Response(404)
        if body is None and url.path.startswith("/wp-json/tribe/"):
            return httpx.Response(
                200,
                content=tribe_body(url),
                headers={"Content-Type": "application/json; charset=utf-8"},
            )
        if body is None and url.params.get("format") == "rss":
            # The Box Shop's seed feed. Its links point at /events/<slug> below.
            return httpx.Response(
                200,
                content=jsonld_seed_body(url),
                headers={"Content-Type": "application/rss+xml; charset=utf-8"},
            )
        if body is None and (url.path == "/calendar/" or url.path.startswith("/events/")):
            return httpx.Response(
                200,
                content=jsonld_body(url),
                headers={"Content-Type": "text/html; charset=utf-8"},
            )
        payload = body if body is not None else feed_body(url)
        return httpx.Response(
            200, content=payload, headers={"Content-Type": "text/calendar; charset=utf-8"}
        )

    return httpx.MockTransport(handler)


def offline_llm() -> LmStudioStatus:
    """LM Studio is not running. The run must not care."""
    return LmStudioStatus(
        available=False, reason="ConnectError: All connection attempts failed"
    )


@pytest.fixture
def env() -> dict[str, str]:
    """A contact that satisfies the loader without naming a real page."""
    return {CONTACT_ENV_VAR: TEST_CONTACT}


@pytest.fixture
def registry(env: dict[str, str]):
    return load_registry(env=env)


def run_main(argv, env, tmp_path: Path, **kwargs) -> int:
    """``main`` with every seam pointed somewhere harmless.

    ``db_path`` included (issue 0014): ``main`` now defaults to the real
    ``db/events.sqlite``, and a test suite that shared one database would carry
    ETags between cases and start answering 304 to itself.
    """
    kwargs.setdefault("transport", calendar_transport())
    kwargs.setdefault("llm_probe", offline_llm)
    kwargs.setdefault("db_path", tmp_path / "events.sqlite")
    return main(
        argv,
        env=dict(env),
        env_file=tmp_path / "absent.env",
        out_dir=tmp_path / "out",
        raw_dir=tmp_path / "raw",
        sleep=noop_sleep,
        clock=FakeClock(),
        now=FIXED_NOW,
        **kwargs,
    )


# --------------------------------------------------------------------------- .env


def test_parse_env_file_handles_the_forms_a_human_writes():
    parsed = parse_env_file(
        "\n".join(
            [
                "# a comment",
                "",
                "MAKER_CALENDAR_CONTACT=https://example.test/about",
                "export QUOTED='single'",
                'DOUBLE="double"',
                "  SPACED  =  value  ",
                "NOT_A_PAIR",
            ]
        )
    )
    assert parsed == {
        "MAKER_CALENDAR_CONTACT": "https://example.test/about",
        "QUOTED": "single",
        "DOUBLE": "double",
        "SPACED": "value",
    }


def test_parse_env_file_keeps_hashes_inside_unquoted_values():
    """A '#' in a URL is part of the URL. Truncating it would be silent damage."""
    assert parse_env_file("URL=https://example.test/a#b") == {
        "URL": "https://example.test/a#b"
    }


def test_load_dotenv_does_not_override_an_already_set_variable(tmp_path: Path):
    """The real environment always wins. This is the whole point of the loader."""
    path = tmp_path / ".env"
    path.write_text("MAKER_CALENDAR_CONTACT=https://from-the-file.test/about\n")

    env = {CONTACT_ENV_VAR: "https://from-the-environment.test/about"}
    applied = load_dotenv(path, env=env)

    assert applied == {}
    assert env[CONTACT_ENV_VAR] == "https://from-the-environment.test/about"


def test_load_dotenv_fills_in_what_launchd_did_not(tmp_path: Path):
    path = tmp_path / ".env"
    path.write_text(f"{CONTACT_ENV_VAR}={TEST_CONTACT}\nOTHER=x\n")

    env: dict[str, str] = {}
    applied = load_dotenv(path, env=env)

    assert applied == {CONTACT_ENV_VAR: TEST_CONTACT, "OTHER": "x"}
    assert env[CONTACT_ENV_VAR] == TEST_CONTACT


def test_load_dotenv_is_a_noop_when_the_file_is_absent(tmp_path: Path):
    env = {"A": "1"}
    assert load_dotenv(tmp_path / "nope.env", env=env) == {}
    assert env == {"A": "1"}


# --------------------------------------------------------------------------- dispatch


def test_every_registry_adapter_has_a_dispatch_entry():
    """``sources.yaml`` names adapters; this is the only place they resolve."""
    assert set(ADAPTERS) == set(KNOWN_ADAPTERS)


def test_only_the_calendar_tribe_and_jsonld_adapters_are_implemented_today():
    assert implemented_adapters() == {"ics", "gcal_ics", "tribe_rest", "jsonld"}


def test_two_adapters_need_the_fetcher_for_follow_up_requests():
    """``tribe_rest`` follows ``next_rest_url``; ``jsonld`` follows a seed list.

    Different reasons, one seam: both get the fetcher and the ``SourceRef`` so
    their extra requests stay inside one rate limiter, one ``robots.txt``
    decision and one ``raw/`` archive.
    """
    assert ADAPTERS["tribe_rest"].paginates is True
    assert ADAPTERS["jsonld"].paginates is True
    assert not any(
        entry.paginates
        for name, entry in ADAPTERS.items()
        if name not in ("tribe_rest", "jsonld")
    )


def test_pending_adapters_name_the_issue_that_implements_them():
    expected = {
        "embedded_json": "0021",
        "json": "0022",
        "nextdata": "0023",
        "bookwhen_html": "0024",
        "rss": "0025",
        "llm_html": "0028",
    }
    for name, issue in expected.items():
        entry = ADAPTERS[name]
        assert not entry.implemented
        assert entry.issue == issue
        assert "not yet implemented" in entry.skip_message
        assert issue in entry.skip_message


def test_runnable_count_matches_the_registry(registry):
    runnable = [ref for ref in registry.all_sources if is_runnable(ref)]
    assert len(runnable) == EXPECTED_RUNNABLE


def test_iter_sources_reports_disabled_and_todo_sources_too(registry):
    """30 in the registry means 30 in the run report, not 26."""
    assert len(list(iter_sources(registry))) == EXPECTED_SOURCES


# --------------------------------------------------------------------------- validate


def test_validate_reports_the_registry_counts(env, tmp_path: Path, capsys):
    code = main(
        ["validate"], env=dict(env), env_file=tmp_path / "absent.env"
    )
    out = capsys.readouterr().out

    assert code == EXIT_OK
    assert f"spaces:  {EXPECTED_SPACES}" in out
    assert (
        f"sources: {EXPECTED_SOURCES} ({EXPECTED_ENABLED} enabled, "
        f"{EXPECTED_DISABLED} disabled, {EXPECTED_TODO} TODO)" in out
    )
    assert f"runnable end to end right now: {EXPECTED_RUNNABLE}" in out


def test_validate_names_implemented_and_pending_adapters(env, tmp_path: Path, capsys):
    main(["validate"], env=dict(env), env_file=tmp_path / "absent.env")
    out = capsys.readouterr().out

    assert "ics" in out and "implemented (issue 0007)" in out
    assert "implemented (issue 0019)" in out  # tribe_rest
    assert "implemented (issue 0020)" in out  # jsonld, since this issue
    assert "NOT implemented (issue 0021)" in out  # embedded_json
    assert "TODO (issue 0002)" in out  # sequoia-fabrica bookwhen-public
    assert "disabled" in out


def test_validate_makes_no_requests(env, tmp_path: Path):
    """No transport is passed, so a request would raise rather than go out."""

    def explode(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError(f"validate must not fetch anything: {request.url}")

    code = main(
        ["validate"],
        env=dict(env),
        env_file=tmp_path / "absent.env",
        transport=httpx.MockTransport(explode),
    )
    assert code == EXIT_OK


# --------------------------------------------------------------------------- startup


def test_missing_contact_is_a_clear_error_and_not_a_traceback(tmp_path: Path, capsys):
    code = main(["validate"], env={}, env_file=tmp_path / "absent.env")
    err = capsys.readouterr().err

    assert code == EXIT_CONFIG
    assert "configuration error" in err
    assert CONTACT_ENV_VAR in err
    assert "Traceback" not in err


def test_placeholder_contact_is_refused(tmp_path: Path, capsys):
    code = main(
        ["validate"],
        env={CONTACT_ENV_VAR: "https://example.com/about"},
        env_file=tmp_path / "absent.env",
    )
    assert code == EXIT_CONFIG
    assert "placeholder" in capsys.readouterr().err


def test_unknown_space_is_a_clear_error(env, tmp_path: Path, capsys):
    code = run_main(["run", "--space", "not-a-space"], env, tmp_path)
    err = capsys.readouterr().err

    assert code == EXIT_CONFIG
    assert "no space with id 'not-a-space'" in err
    assert "Traceback" not in err


# --------------------------------------------------------------------------- the run


def test_dry_run_writes_staging_and_leaves_out_alone(env, tmp_path: Path, capsys):
    out_dir = tmp_path / "out"
    code = run_main(["run", "--dry-run"], env, tmp_path)
    capsys.readouterr()

    staging = out_dir / STAGING_DIRNAME
    assert code == EXIT_OK
    assert (staging / "calendar.ics").is_file()
    assert (staging / "calendar.ics").read_bytes().startswith(b"BEGIN:VCALENDAR")
    # Both artifacts or neither, since issue 0018. See tests/test_emit_rss.py.
    assert (staging / "feed.xml").is_file()
    assert (staging / "feed.xml").read_bytes().startswith(b"<?xml")
    assert not (out_dir / "calendar.ics").exists()
    assert not (out_dir / "feed.xml").exists()
    assert not (out_dir / "spaces").exists()


def test_dry_run_publishes_every_runnable_source(env, tmp_path: Path, registry):
    report = run_pipeline(
        registry,
        dry_run=True,
        out_dir=tmp_path / "out",
        raw_dir=tmp_path / "raw",
        transport=calendar_transport(),
        sleep=noop_sleep,
        clock=FakeClock(),
        now=FIXED_NOW,
        llm_probe=offline_llm,
    )

    assert len(report.records) == EXPECTED_SOURCES
    assert len(report.ran) == EXPECTED_RUNNABLE
    # One VEVENT per feed, every one inside the horizon.
    assert all(record.horizon_count == 1 for record in report.ran)

    # Three of the sixteen carry a `location_contains` filter that the
    # fixture's Oakland address does not match, so they legitimately keep
    # nothing. Filters drop events silently — the point of asserting on the
    # count rather than on 16 is that the drop shows up here if it changes.
    filtered_out = sum(record.filtered_out_count for record in report.ran)
    assert filtered_out == 3
    assert report.event_count == EXPECTED_RUNNABLE - filtered_out == 13
    assert report.exit_code == EXIT_OK
    assert report.published is True


def test_unimplemented_adapter_is_skipped_with_its_issue_number(
    env, tmp_path: Path, registry
):
    report = run_pipeline(
        registry,
        space_id="the-crucible",  # embedded_json + json + nextdata, none implemented
        out_dir=tmp_path / "out",
        raw_dir=tmp_path / "raw",
        transport=calendar_transport(),
        sleep=noop_sleep,
        clock=FakeClock(),
        now=FIXED_NOW,
        llm_probe=offline_llm,
    )

    record = report.record_for("the-crucible", "woocommerce-store-api")
    assert record is not None
    assert record.status == "skipped"
    assert record.skipped_because == "adapter_not_implemented"
    assert "not yet implemented" in (record.reason or "")
    assert "0022" in (record.reason or "")
    # Skipped, not crashed: the run finished and reported the whole space.
    assert len(report.records) == 4
    assert report.exit_code == EXIT_OK


def test_a_space_of_unimplemented_adapters_publishes_nothing(
    env, tmp_path: Path, registry
):
    """Refusing to write an empty calendar is not the same as failing."""
    report = run_pipeline(
        registry,
        space_id="the-crucible",
        out_dir=tmp_path / "out",
        raw_dir=tmp_path / "raw",
        transport=calendar_transport(),
        sleep=noop_sleep,
        clock=FakeClock(),
        now=FIXED_NOW,
        llm_probe=offline_llm,
    )

    assert report.event_count == 0
    assert report.published is False
    assert "empty one" in (report.publish_skipped_reason or "")
    assert not (tmp_path / "out" / STAGING_DIRNAME / "calendar.ics").exists()
    assert report.exit_code == EXIT_OK


def test_todo_source_is_skipped_with_a_clear_message(env, tmp_path: Path, registry):
    """Sequoia Fabrica's Bookwhen token is still missing — issue 0002."""
    report = run_pipeline(
        registry,
        space_id="sequoia-fabrica",
        out_dir=tmp_path / "out",
        raw_dir=tmp_path / "raw",
        transport=calendar_transport(),
        sleep=noop_sleep,
        clock=FakeClock(),
        now=FIXED_NOW,
        llm_probe=offline_llm,
    )

    record = report.record_for("sequoia-fabrica", "bookwhen-public")
    assert record is not None
    assert record.status == "skipped"
    assert record.skipped_because == "todo"
    assert "0002" in (record.reason or "")
    assert record.bytes == 0


def test_space_restricts_the_run_to_one_space(env, tmp_path: Path, capsys):
    code = run_main(["run", "--space", "hacker-dojo"], env, tmp_path)
    out = capsys.readouterr().out

    assert code == EXIT_OK
    assert "hacker-dojo:meetup-ical" in out
    assert "hacker-dojo:luma-calendar" in out
    assert "sudo-room" not in out
    assert "ace-makerspace" not in out


def test_space_run_never_touches_the_published_calendar(env, tmp_path: Path, capsys):
    """A single space's events are not the merged calendar. Staging, always."""
    out_dir = tmp_path / "out"
    code = run_main(["run", "--space", "hacker-dojo"], env, tmp_path)
    capsys.readouterr()

    assert code == EXIT_OK
    assert (out_dir / STAGING_DIRNAME / "calendar.ics").is_file()
    assert not (out_dir / "calendar.ics").exists()


def test_exit_code_is_zero_on_success(env, tmp_path: Path, capsys):
    code = run_main(["run", "--dry-run"], env, tmp_path)
    capsys.readouterr()
    assert code == EXIT_OK


def test_horizon_days_override_reaches_the_adapter(env, tmp_path: Path, registry):
    report = run_pipeline(
        registry,
        space_id="hacker-dojo",
        horizon_days=1,
        out_dir=tmp_path / "out",
        raw_dir=tmp_path / "raw",
        transport=calendar_transport(),
        sleep=noop_sleep,
        clock=FakeClock(),
        now=FIXED_NOW,
        llm_probe=offline_llm,
    )

    assert report.horizon_days == 1
    # The fixture event is five days out, so a one-day horizon clips it away —
    # and a clipped feed is still ok=True with zero events, not a failure.
    for record in report.ran:
        assert record.status == "ok"
        assert record.raw_count == 1
        assert record.horizon_count == 0
    assert report.event_count == 0


# --------------------------------------------------------------------------- the model


def test_lm_studio_being_down_does_not_stop_the_calendar(env, tmp_path: Path, capsys):
    code = run_main(["run", "--dry-run"], env, tmp_path, llm_probe=offline_llm)
    out = capsys.readouterr().out

    assert code == EXIT_OK
    assert (tmp_path / "out" / STAGING_DIRNAME / "calendar.ics").is_file()
    assert "LM Studio is not answering" in out


def test_no_llm_skips_the_probe_entirely(env, tmp_path: Path, registry):
    def must_not_run() -> LmStudioStatus:  # pragma: no cover
        raise AssertionError("--no-llm must not probe LM Studio")

    report = run_pipeline(
        registry,
        space_id="hacker-dojo",
        no_llm=True,
        out_dir=tmp_path / "out",
        raw_dir=tmp_path / "raw",
        transport=calendar_transport(),
        sleep=noop_sleep,
        clock=FakeClock(),
        now=FIXED_NOW,
        llm_probe=must_not_run,
    )

    assert report.lm_studio is not None
    assert report.lm_studio.checked is False
    assert report.enrich_skipped_reason == "--no-llm was passed; the model stages are off"


def test_lm_studio_up_still_skips_enrich_until_issue_0029(env, tmp_path: Path, registry):
    report = run_pipeline(
        registry,
        space_id="hacker-dojo",
        out_dir=tmp_path / "out",
        raw_dir=tmp_path / "raw",
        transport=calendar_transport(),
        sleep=noop_sleep,
        clock=FakeClock(),
        now=FIXED_NOW,
        llm_probe=lambda: LmStudioStatus(available=True, model_count=1),
    )
    assert report.enrich_skipped_reason == "enrich is not implemented yet (issue 0029)"


# --------------------------------------------------------------------------- health


def test_health_gates_run_and_say_so():
    """Issue 0016 filled the seam in. The gates are real and they report it.

    With no records and no store there is nothing to compare against, so the
    honest answer is "not blocked" — but ``implemented`` is now true, and
    ``health.json`` says gates ran rather than that none did.
    """
    decision = evaluate_health([])
    assert decision.blocked is False
    assert decision.implemented is True
    assert decision.gate_issue == "0016"


def test_blocked_health_gates_exit_non_zero(env, tmp_path: Path, registry, monkeypatch):
    """The contract issue 0016 inherits: a blocked publish is a non-zero exit."""
    monkeypatch.setattr(
        "pipeline.cli.evaluate_health",
        lambda records, **kwargs: HealthDecision(
            blocked=True, reasons=("global count dropped 62%",), implemented=True
        ),
    )
    report = run_pipeline(
        registry,
        space_id="hacker-dojo",
        out_dir=tmp_path / "out",
        raw_dir=tmp_path / "raw",
        transport=calendar_transport(),
        sleep=noop_sleep,
        clock=FakeClock(),
        now=FIXED_NOW,
        llm_probe=offline_llm,
    )

    assert report.exit_code == EXIT_HEALTH_BLOCKED
    assert report.published is False
    assert not (tmp_path / "out" / STAGING_DIRNAME / "calendar.ics").exists()
    assert "global count dropped 62%" in (report.publish_skipped_reason or "")


# --------------------------------------------------------------------------- records


def test_source_record_carries_what_health_json_needs():
    """Issue 0017 builds ``health.json`` out of exactly these fields."""
    record = SourceRecord(space_id="s", label="l", adapter="ics")
    data = record.as_dict()
    for key in (
        "status",
        "content_type",
        "bytes",
        "raw_count",
        "horizon_count",
        "elapsed_seconds",
    ):
        assert key in data


def test_run_records_are_structured_and_json_ready(env, tmp_path: Path, registry):
    import json

    report = run_pipeline(
        registry,
        space_id="hacker-dojo",
        out_dir=tmp_path / "out",
        raw_dir=tmp_path / "raw",
        transport=calendar_transport(),
        sleep=noop_sleep,
        clock=FakeClock(),
        now=FIXED_NOW,
        llm_probe=offline_llm,
    )

    payload = json.loads(json.dumps(report.as_dict()))
    assert payload["counts"]["sources"] == 2
    assert payload["counts"]["events"] == 2
    assert payload["health"]["implemented"] is True
    assert payload["health"]["blocked"] is False
    assert len(payload["sources"]) == 2

    record = payload["sources"][0]
    assert record["status"] == "ok"
    assert record["content_type"] == "text/calendar"
    assert record["bytes"] > 0
    assert record["raw_count"] == 1
    assert record["horizon_count"] == 1
    assert record["event_count"] == 1
    assert record["elapsed_seconds"] >= 0.0
    assert record["last_change"] == "2026-08-04T12:00:00+00:00"


def test_raw_and_horizon_counts_are_reported_separately(env, tmp_path: Path, registry):
    """Sequoia Fabrica ships 89 VEVENTs for ~7 live events. Gates count the second."""
    body = ICS_BODY.replace(
        b"END:VCALENDAR\r\n",
        b"BEGIN:VEVENT\r\nUID:evt-old@test\r\nDTSTAMP:20240104T120000Z\r\n"
        b"DTSTART:20240110T180000Z\r\nDTEND:20240110T200000Z\r\n"
        b"SUMMARY:Long gone\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n",
    )
    report = run_pipeline(
        registry,
        space_id="hacker-dojo",
        out_dir=tmp_path / "out",
        raw_dir=tmp_path / "raw",
        transport=calendar_transport(body),
        sleep=noop_sleep,
        clock=FakeClock(),
        now=FIXED_NOW,
        llm_probe=offline_llm,
    )

    record = report.records[0]
    assert record.raw_count == 2  # VEVENTs in the file
    assert record.horizon_count == 1  # post-expansion, inside the horizon
    assert record.event_count == 1


def test_a_source_answering_html_is_reported_not_crashed(env, tmp_path: Path, registry):
    """"HTTP 200 is not success": ?ical=1 returning the homepage, live."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200,
            content=b"<!doctype html><html><body>Welcome</body></html>",
            headers={"Content-Type": "text/html"},
        )

    report = run_pipeline(
        registry,
        space_id="hacker-dojo",
        out_dir=tmp_path / "out",
        raw_dir=tmp_path / "raw",
        transport=httpx.MockTransport(handler),
        sleep=noop_sleep,
        clock=FakeClock(),
        now=FIXED_NOW,
        llm_probe=offline_llm,
    )

    for record in report.records:
        assert record.status == "failed"
        assert record.content_type == "text/html"
        assert record.problem == "wrong_content_type"
        assert record.error
    assert report.event_count == 0
    assert report.published is False
    assert report.exit_code == EXIT_OK


def test_robots_disallow_is_reported_as_blocked(env, tmp_path: Path, registry):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200, content=b"User-agent: *\nDisallow: /\n", headers={"Content-Type": "text/plain"}
            )
        raise AssertionError(f"must not fetch a disallowed path: {request.url}")

    report = run_pipeline(
        registry,
        space_id="hacker-dojo",
        out_dir=tmp_path / "out",
        raw_dir=tmp_path / "raw",
        transport=httpx.MockTransport(handler),
        sleep=noop_sleep,
        clock=FakeClock(),
        now=FIXED_NOW,
        llm_probe=offline_llm,
    )

    assert [record.status for record in report.records] == ["blocked", "blocked"]
    assert report.exit_code == EXIT_OK


def test_raw_archive_is_written_before_anything_parses(env, tmp_path: Path, registry):
    """``raw/`` is the evidence trail that answers "source or us?" in ten seconds."""
    raw_dir = tmp_path / "raw"
    run_pipeline(
        registry,
        space_id="hacker-dojo",
        out_dir=tmp_path / "out",
        raw_dir=raw_dir,
        transport=calendar_transport(),
        sleep=noop_sleep,
        clock=FakeClock(),
        now=FIXED_NOW,
        llm_probe=offline_llm,
    )

    day = raw_dir / FIXED_NOW.date().isoformat()
    written = sorted(path.name for path in day.iterdir())
    assert written == ["hacker-dojo-luma-calendar.ics", "hacker-dojo-meetup-ical.ics"]
    assert (day / written[0]).read_bytes().startswith(b"BEGIN:VCALENDAR")


def test_one_event_syndicated_through_two_feeds_is_merged_not_duplicated(
    env, tmp_path: Path, registry
):
    """Both of Hacker Dojo's feeds pinned to one event.

    Since issue 0015 this is dedupe's case rather than the floor's: same space,
    same start, identical title. It merges, the higher-``trust`` feed's record
    survives, and the publish is unaffected — which is what
    ``collapse_uid_collisions`` was standing in for.
    """
    report = run_pipeline(
        registry,
        space_id="hacker-dojo",
        out_dir=tmp_path / "out",
        raw_dir=tmp_path / "raw",
        transport=calendar_transport(ICS_BODY),  # identical event from both feeds
        sleep=noop_sleep,
        clock=FakeClock(),
        now=FIXED_NOW,
        llm_probe=offline_llm,
    )

    assert report.merge_count == 1
    assert report.dedupe_result is not None
    merge = report.dedupe_result.merges[0]
    assert merge.winner_source_label == "meetup-ical"  # trust 100
    assert merge.loser_source_label == "luma-calendar"  # trust 90
    assert report.event_count == 1
    assert report.published is True
    assert report.exit_code == EXIT_OK
    assert (tmp_path / "out" / STAGING_DIRNAME / "calendar.ics").is_file()


def test_a_uid_collision_outside_dedupes_window_is_still_collapsed(
    env, tmp_path: Path, registry
):
    """The floor beneath dedupe, and why it is still wanted.

    ``emit_ics`` refuses a calendar with two VEVENTs sharing a UID, and it is
    right to — but that refusal is fatal to the entire run. Dedupe cannot help
    here: these two events share a UID while starting five hours apart under
    different names, so no start-plus-title rule will ever pair them.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        body = ICS_BODY
        if "meetup" not in str(request.url):  # the Luma feed; lu.ma, not "luma"
            # Same UID, five hours later, a different event entirely.
            body = ICS_BODY.replace(b"DTSTART:20260810T180000Z", b"DTSTART:20260810T230000Z")
            body = body.replace(b"DTEND:20260810T200000Z", b"DTEND:20260811T010000Z")
            body = body.replace(b"SUMMARY:Open Shop Night", b"SUMMARY:Board Game Evening")
        return httpx.Response(
            200, content=body, headers={"Content-Type": "text/calendar; charset=utf-8"}
        )

    report = run_pipeline(
        registry,
        space_id="hacker-dojo",
        out_dir=tmp_path / "out",
        raw_dir=tmp_path / "raw",
        transport=httpx.MockTransport(handler),
        sleep=noop_sleep,
        clock=FakeClock(),
        now=FIXED_NOW,
        llm_probe=offline_llm,
    )

    assert report.merge_count == 0
    assert report.uid_collisions == ["hacker-dojo:evt-1@test"]
    assert report.event_count == 1
    assert report.published is True
    assert report.exit_code == EXIT_OK
    assert (tmp_path / "out" / STAGING_DIRNAME / "calendar.ics").is_file()
