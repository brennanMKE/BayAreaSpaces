"""Tests for the HTTP layer (issue 0006).

**No test here touches the network.** Every request goes through
``httpx.MockTransport``. That is not merely hygiene: issue 0001 (the bot about
page must exist before we fetch anything) is still open, and
``$MAKER_CALENDAR_CONTACT`` is not set on this machine, so a live request would
be made under a User-Agent pointing at a page that does not resolve — exactly
the "bot performing accountability" failure CLAUDE.md calls out.

Three things are being defended.

**Good citizenship.** Rate limiting is per *host* and not per space, because
several spaces publish through ``api.lu.ma``. ``robots.txt`` disallow means we
do not fetch, not that we find another route. The User-Agent never impersonates
anything.

**Faithful reporting.** "HTTP 200 is not success." Status, content type, body
and byte count come back as four separate fields, and the layer never collapses
them into a verdict. The registry contains, live, a 404 with a valid RSS body
and a 200 with ``text/html`` for ``?format=ical``; an adapter that cannot see
both halves of those cannot do its job.

**Evidence.** Every payload lands in ``raw/`` verbatim before anything parses
it, and ``raw/`` is never mutated.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from pipeline.config import CONTACT_ENV_VAR, SOURCES_YAML, Registry, load_registry
from pipeline.fetch import (
    RETRY_STATUS_CODES,
    ContentTypeMismatch,
    DishonestUserAgentError,
    Fetcher,
    FetchResult,
    HttpStateStore,
    InMemoryStateStore,
    Outcome,
    RateLimiter,
    SourceState,
    archive_extension,
    archive_raw,
    assert_honest_user_agent,
    host_of,
    origin_of,
    request_url,
    resolve_url,
    source_key,
    split_content_type,
)

TEST_CONTACT = "https://maker-calendar.test/about"
USER_AGENT = f"bayarea-maker-calendar/0.1 (+{TEST_CONTACT})"
FIXED_NOW = datetime(2026, 8, 5, 3, 15, 0, tzinfo=timezone.utc)

ICS_BODY = b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n"
RSS_BODY = (
    b'<?xml version="1.0"?><rss version="2.0"><channel><title>Events</title>'
    b"<item><title>Open Shop Night</title></channel></rss>"
)
HOMEPAGE_BODY = b"<!doctype html><html><body><h1>Welcome to the space</h1></body></html>"


# --------------------------------------------------------------------------- harness


class FakeClock:
    """A monotonic clock that only advances when something sleeps.

    Lets the rate-limiting tests assert on exact durations without spending
    them. Sleeps are recorded in order.
    """

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds

    @property
    def total_slept(self) -> float:
        return sum(self.sleeps)


def reply(
    status: int = 200,
    body: bytes = b"",
    content_type: str | None = None,
    headers: dict[str, str] | None = None,
):
    """A response factory. Fresh ``httpx.Response`` per call, so it can repeat."""

    def build(request: httpx.Request) -> httpx.Response:
        all_headers = dict(headers or {})
        if content_type is not None:
            all_headers["Content-Type"] = content_type
        return httpx.Response(status, content=body, headers=all_headers)

    return build


def boom(exc: Exception):
    """A route that raises a transport error."""

    def build(request: httpx.Request) -> httpx.Response:
        raise exc

    return build


class Router:
    """A tiny ``httpx.MockTransport`` handler with per-URL response queues."""

    def __init__(self, robots: dict[str, object] | None = None) -> None:
        # host -> robots.txt body (str), status (int) or callable. Default 404,
        # which means "no robots.txt, everything allowed".
        self.robots: dict[str, object] = robots or {}
        self.requests: list[httpx.Request] = []
        self._routes: dict[str, list] = {}

    def add(self, url: str, *responses) -> Router:
        self._routes.setdefault(url, []).extend(responses)
        return self

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/robots.txt":
            spec = self.robots.get(request.url.host, 404)
            if isinstance(spec, int):
                return httpx.Response(spec)
            if callable(spec):
                return spec(request)
            return httpx.Response(
                200, content=str(spec).encode(), headers={"Content-Type": "text/plain"}
            )
        queue = self._routes.get(str(request.url))
        if not queue:
            raise AssertionError(f"unexpected request to {request.url}")
        item = queue[0] if len(queue) == 1 else queue.pop(0)
        return item(request)

    @property
    def target_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path != "/robots.txt"]

    @property
    def robots_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == "/robots.txt"]

    @property
    def target_urls(self) -> list[str]:
        return [str(r.url) for r in self.target_requests]


def make_registry(*spaces: dict, user_agent: str = USER_AGENT, rate_limit: float = 2.0) -> Registry:
    return Registry.model_validate(
        {
            "defaults": {
                "timezone": "America/Los_Angeles",
                "user_agent": user_agent,
                "rate_limit_seconds": rate_limit,
                "horizon_days": 120,
            },
            "spaces": list(spaces),
        }
    )


def space(space_id: str, *sources: dict, rate_limit: float | None = None) -> dict:
    data = {
        "id": space_id,
        "name": space_id.title(),
        "city": "Oakland",
        "region": "East Bay",
        "url": f"https://{space_id}.example.org",
        "sources": list(sources),
    }
    if rate_limit is not None:
        data["rate_limit_seconds"] = rate_limit
    return data


def make_fetcher(
    registry: Registry,
    router: Router,
    tmp_path: Path,
    *,
    clock: FakeClock | None = None,
    **kwargs,
) -> tuple[Fetcher, FakeClock]:
    clock = clock or FakeClock()
    fetcher = Fetcher(
        registry,
        transport=httpx.MockTransport(router),
        raw_dir=tmp_path / "raw",
        sleep=clock.sleep,
        clock=clock,
        now=lambda: FIXED_NOW,
        **kwargs,
    )
    return fetcher, clock


def only(registry: Registry) -> tuple:
    return next(registry.iter_enabled())


# --------------------------------------------------------------------------- rate limit


def test_rate_limiter_waits_the_full_delay_between_requests_to_one_host():
    clock = FakeClock()
    limiter = RateLimiter(sleep=clock.sleep, clock=clock)
    assert limiter.wait("api.lu.ma", 2.0) == 0.0
    assert limiter.wait("api.lu.ma", 2.0) == 2.0
    assert clock.sleeps == [2.0]


def test_rate_limiter_only_waits_out_the_remainder():
    clock = FakeClock()
    limiter = RateLimiter(sleep=clock.sleep, clock=clock)
    limiter.wait("api.lu.ma", 2.0)
    clock.advance(1.5)  # the request itself took 1.5 s
    assert limiter.wait("api.lu.ma", 2.0) == pytest.approx(0.5)


def test_rate_limiter_does_not_make_one_host_wait_for_another():
    clock = FakeClock()
    limiter = RateLimiter(sleep=clock.sleep, clock=clock)
    limiter.wait("api.lu.ma", 2.0)
    assert limiter.wait("www.acemakerspace.org", 2.0) == 0.0
    assert clock.sleeps == []


def test_rate_limiting_is_per_host_not_per_space(tmp_path):
    """Four spaces publish through api.lu.ma; they must queue behind each other."""
    registry = make_registry(
        space("frontier", {"adapter": "ics", "url": "https://api.lu.ma/ics/get?a=1", "label": "luma"}),
        space("humanmade", {"adapter": "ics", "url": "https://api.lu.ma/ics/get?a=2", "label": "luma"}),
    )
    router = Router()
    router.add("https://api.lu.ma/ics/get?a=1", reply(body=ICS_BODY, content_type="text/calendar"))
    router.add("https://api.lu.ma/ics/get?a=2", reply(body=ICS_BODY, content_type="text/calendar"))

    fetcher, clock = make_fetcher(registry, router, tmp_path)
    results = list(fetcher.fetch_all())

    assert [r.outcome for r in results] == [Outcome.FETCHED, Outcome.FETCHED]
    # robots.txt (free) -> feed 1 (2 s after robots) -> feed 2 (2 s after feed 1).
    assert clock.sleeps == [2.0, 2.0]
    assert len(router.robots_requests) == 1, "robots.txt is cached per host per run"
    assert results[1].rate_limit_delay_seconds == 2.0


def test_a_spaces_rate_limit_override_is_honored(tmp_path):
    """Ace sets 10 s because its robots.txt sets Crawl-delay: 10."""
    registry = make_registry(
        space(
            "ace",
            {"adapter": "ics", "url": "https://www.acemakerspace.org/a.ics", "label": "a"},
            {"adapter": "ics", "url": "https://www.acemakerspace.org/b.ics", "label": "b"},
            rate_limit=10.0,
        )
    )
    router = Router()
    router.add("https://www.acemakerspace.org/a.ics", reply(body=ICS_BODY, content_type="text/calendar"))
    router.add("https://www.acemakerspace.org/b.ics", reply(body=ICS_BODY, content_type="text/calendar"))

    fetcher, clock = make_fetcher(registry, router, tmp_path)
    list(fetcher.fetch_all())

    assert clock.sleeps == [10.0, 10.0]


def test_crawl_delay_raises_a_smaller_registry_limit(tmp_path):
    registry = make_registry(
        space(
            "ace",
            {"adapter": "ics", "url": "https://www.acemakerspace.org/a.ics", "label": "a"},
            {"adapter": "ics", "url": "https://www.acemakerspace.org/b.ics", "label": "b"},
        )
    )
    router = Router(robots={"www.acemakerspace.org": "User-agent: *\nCrawl-delay: 10\nAllow: /\n"})
    router.add("https://www.acemakerspace.org/a.ics", reply(body=ICS_BODY, content_type="text/calendar"))
    router.add("https://www.acemakerspace.org/b.ics", reply(body=ICS_BODY, content_type="text/calendar"))

    fetcher, clock = make_fetcher(registry, router, tmp_path)
    list(fetcher.fetch_all())

    # The registry default is 2; Crawl-delay: 10 wins because it is larger.
    assert clock.sleeps == [10.0, 10.0]


def test_crawl_delay_never_lowers_the_registry_limit(tmp_path):
    registry = make_registry(
        space(
            "ace",
            {"adapter": "ics", "url": "https://www.acemakerspace.org/a.ics", "label": "a"},
            {"adapter": "ics", "url": "https://www.acemakerspace.org/b.ics", "label": "b"},
            rate_limit=10.0,
        )
    )
    router = Router(robots={"www.acemakerspace.org": "User-agent: *\nCrawl-delay: 1\n"})
    router.add("https://www.acemakerspace.org/a.ics", reply(body=ICS_BODY, content_type="text/calendar"))
    router.add("https://www.acemakerspace.org/b.ics", reply(body=ICS_BODY, content_type="text/calendar"))

    fetcher, clock = make_fetcher(registry, router, tmp_path)
    list(fetcher.fetch_all())

    assert clock.sleeps == [10.0, 10.0]


def test_host_and_origin_helpers():
    assert host_of("https://API.LU.MA:443/ics/get?x=1") == "api.lu.ma"
    assert origin_of("https://API.LU.MA/ics/get?x=1") == "https://api.lu.ma"


# --------------------------------------------------------------------------- robots.txt


def test_robots_disallow_blocks_the_fetch(tmp_path):
    registry = make_registry(
        space("boxshop", {"adapter": "json", "url": "https://www.lower48.org/events?format=json",
         "shape": "woocommerce_store_products", "label": "json"})
    )
    router = Router(robots={"www.lower48.org": "User-agent: *\nDisallow: /events?format=json\n"})

    fetcher, _ = make_fetcher(registry, router, tmp_path)
    result = fetcher.fetch(only(registry))

    assert result.outcome is Outcome.BLOCKED
    assert result.blocked and not result.failed
    assert result.body is None and result.status_code is None
    assert "disallows" in (result.reason or "")
    assert router.target_requests == [], "a disallow means we do not fetch it at all"


def test_robots_allows_the_permitted_route_on_the_same_host(tmp_path):
    """The Box Shop disallows ?format=json and ?format=ical, allows ?format=rss."""
    registry = make_registry(
        space("boxshop", {"adapter": "rss", "url": "https://www.lower48.org/events?format=rss", "label": "rss"})
    )
    robots = "User-agent: *\nDisallow: /events?format=json\nDisallow: /events?format=ical\n"
    router = Router(robots={"www.lower48.org": robots})
    router.add(
        "https://www.lower48.org/events?format=rss",
        reply(body=RSS_BODY, content_type="application/rss+xml"),
    )

    fetcher, _ = make_fetcher(registry, router, tmp_path)
    result = fetcher.fetch(only(registry))

    assert result.outcome is Outcome.FETCHED
    assert result.body == RSS_BODY


def test_path_level_disallows_bind_even_when_a_named_group_would_permit(tmp_path):
    """CLAUDE.md: path disallows bind regardless of which group we match."""
    registry = make_registry(
        space("boxshop", {"adapter": "ics", "url": "https://www.lower48.org/events?format=ical", "label": "ical"})
    )
    robots = (
        "User-agent: bayarea-maker-calendar\n"
        "Allow: /\n"
        "\n"
        "User-agent: *\n"
        "Disallow: /events?format=ical\n"
    )
    router = Router(robots={"www.lower48.org": robots})

    fetcher, _ = make_fetcher(registry, router, tmp_path)
    result = fetcher.fetch(only(registry))

    assert result.outcome is Outcome.BLOCKED
    assert router.target_requests == []


def test_ai_agent_disallows_do_not_apply_to_this_pipeline(tmp_path):
    """lower48.org names ClaudeBot, GPTBot and 21 others; we are none of them."""
    registry = make_registry(
        space("boxshop", {"adapter": "rss", "url": "https://www.lower48.org/events?format=rss", "label": "rss"})
    )
    robots = (
        "User-agent: ClaudeBot\nDisallow: /\n\n"
        "User-agent: anthropic-ai\nDisallow: /\n\n"
        "User-agent: GPTBot\nDisallow: /\n\n"
        "User-agent: CCBot\nDisallow: /\n\n"
        "User-agent: *\nAllow: /\n"
    )
    router = Router(robots={"www.lower48.org": robots})
    router.add(
        "https://www.lower48.org/events?format=rss",
        reply(body=RSS_BODY, content_type="application/rss+xml"),
    )

    fetcher, _ = make_fetcher(registry, router, tmp_path)
    result = fetcher.fetch(only(registry))

    assert result.outcome is Outcome.FETCHED


def test_missing_robots_txt_allows_everything(tmp_path):
    registry = make_registry(space("sudo", {"adapter": "ics", "url": "https://sudoroom.org/c.ics", "label": "ics"}))
    router = Router(robots={"sudoroom.org": 404})
    router.add("https://sudoroom.org/c.ics", reply(body=ICS_BODY, content_type="text/calendar"))

    fetcher, _ = make_fetcher(registry, router, tmp_path)
    assert fetcher.fetch(only(registry)).outcome is Outcome.FETCHED


def test_unreachable_robots_txt_fails_the_source_rather_than_fetching(tmp_path):
    """RFC 9309: a 5xx on robots.txt means assume disallow. We do, and we report
    it as ``failed`` so issue 0014 carries the space forward."""
    registry = make_registry(space("sudo", {"adapter": "ics", "url": "https://sudoroom.org/c.ics", "label": "ics"}))
    router = Router(robots={"sudoroom.org": 503})

    fetcher, _ = make_fetcher(registry, router, tmp_path)
    result = fetcher.fetch(only(registry))

    assert result.outcome is Outcome.FAILED
    assert "robots.txt" in (result.reason or "")
    assert router.target_requests == []


def test_robots_txt_is_fetched_once_per_host_per_run(tmp_path):
    registry = make_registry(
        space(
            "sudo",
            {"adapter": "ics", "url": "https://sudoroom.org/a.ics", "label": "a"},
            {"adapter": "ics", "url": "https://sudoroom.org/b.ics", "label": "b"},
        ),
        space("other", {"adapter": "ics", "url": "https://example.net/c.ics", "label": "c"}),
    )
    router = Router()
    for url in ("https://sudoroom.org/a.ics", "https://sudoroom.org/b.ics", "https://example.net/c.ics"):
        router.add(url, reply(body=ICS_BODY, content_type="text/calendar"))

    fetcher, _ = make_fetcher(registry, router, tmp_path)
    list(fetcher.fetch_all())

    assert len(router.robots_requests) == 2
    assert {r.url.host for r in router.robots_requests} == {"sudoroom.org", "example.net"}


# --------------------------------------------------------------------------- user agent


def test_the_user_agent_is_sent_on_every_request(tmp_path):
    registry = make_registry(space("sudo", {"adapter": "ics", "url": "https://sudoroom.org/c.ics", "label": "ics"}))
    router = Router()
    router.add("https://sudoroom.org/c.ics", reply(body=ICS_BODY, content_type="text/calendar"))

    fetcher, _ = make_fetcher(registry, router, tmp_path)
    fetcher.fetch(only(registry))

    assert [r.headers["user-agent"] for r in router.requests] == [USER_AGENT, USER_AGENT]


@pytest.mark.parametrize(
    "agent",
    [
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        "Googlebot/2.1 (+http://www.google.com/bot.html)",
        "ClaudeBot/1.0 (+https://anthropic.com/claudebot)",
        "curl/8.4.0 (+https://example.org/about)",
    ],
)
def test_impersonating_user_agents_are_refused(agent):
    with pytest.raises(DishonestUserAgentError):
        assert_honest_user_agent(agent)


def test_a_user_agent_without_a_contact_url_is_refused():
    with pytest.raises(DishonestUserAgentError, match="contact"):
        assert_honest_user_agent("bayarea-maker-calendar/0.1")


def test_the_fetcher_refuses_to_start_with_a_dishonest_user_agent(tmp_path):
    registry = make_registry(
        space("sudo", {"adapter": "ics", "url": "https://sudoroom.org/c.ics", "label": "ics"}),
        user_agent="Mozilla/5.0 (compatible)",
    )
    with pytest.raises(DishonestUserAgentError):
        Fetcher(registry, transport=httpx.MockTransport(Router()), raw_dir=tmp_path / "raw")


def test_the_real_registrys_user_agent_is_honest():
    registry = load_registry(SOURCES_YAML, env={CONTACT_ENV_VAR: TEST_CONTACT})
    assert assert_honest_user_agent(registry.user_agent) == registry.user_agent


# --------------------------------------------------------------------------- conditional GET


def test_stored_validators_are_sent_as_conditional_headers(tmp_path):
    registry = make_registry(space("sudo", {"adapter": "ics", "url": "https://sudoroom.org/c.ics", "label": "ics"}))
    router = Router()
    router.add("https://sudoroom.org/c.ics", reply(body=ICS_BODY, content_type="text/calendar"))
    state = InMemoryStateStore(
        {"sudo:ics": SourceState(etag='W/"abc"', last_modified="Tue, 04 Aug 2026 10:00:00 GMT")}
    )

    fetcher, _ = make_fetcher(registry, router, tmp_path, state=state)
    result = fetcher.fetch(only(registry))

    request = router.target_requests[0]
    assert request.headers["if-none-match"] == 'W/"abc"'
    assert request.headers["if-modified-since"] == "Tue, 04 Aug 2026 10:00:00 GMT"
    assert result.conditional is True


def test_a_304_is_reported_as_not_modified_with_no_body(tmp_path):
    registry = make_registry(space("sudo", {"adapter": "ics", "url": "https://sudoroom.org/c.ics", "label": "ics"}))
    router = Router()
    router.add("https://sudoroom.org/c.ics", reply(status=304))
    state = InMemoryStateStore({"sudo:ics": SourceState(etag='W/"abc"')})

    fetcher, _ = make_fetcher(registry, router, tmp_path, state=state)
    result = fetcher.fetch(only(registry))

    assert result.outcome is Outcome.NOT_MODIFIED
    assert result.not_modified and not result.failed
    assert result.status_code == 304
    assert result.body is None
    assert result.byte_count == 0
    assert result.has_body is False
    assert not (tmp_path / "raw").exists(), "a 304 has no payload to archive"


def test_a_304_bumps_last_seen_and_keeps_the_stored_validator(tmp_path):
    registry = make_registry(space("sudo", {"adapter": "ics", "url": "https://sudoroom.org/c.ics", "label": "ics"}))
    router = Router()
    router.add("https://sudoroom.org/c.ics", reply(status=304))
    state = InMemoryStateStore({"sudo:ics": SourceState(etag='W/"abc"', last_content_hash="deadbeef")})

    fetcher, _ = make_fetcher(registry, router, tmp_path, state=state)
    fetcher.fetch(only(registry))

    stored = state.get("sudo:ics")
    assert stored is not None
    assert stored.etag == 'W/"abc"'
    assert stored.last_status == 304
    assert stored.last_seen == FIXED_NOW
    assert stored.last_success_at == FIXED_NOW
    assert stored.last_content_hash == "deadbeef"
    assert stored.consecutive_failures == 0


def test_a_200_stores_the_new_validators(tmp_path):
    registry = make_registry(space("sudo", {"adapter": "ics", "url": "https://sudoroom.org/c.ics", "label": "ics"}))
    router = Router()
    router.add(
        "https://sudoroom.org/c.ics",
        reply(
            body=ICS_BODY,
            content_type="text/calendar",
            headers={"ETag": 'W/"new"', "Last-Modified": "Wed, 05 Aug 2026 01:00:00 GMT"},
        ),
    )
    state = InMemoryStateStore()

    fetcher, _ = make_fetcher(registry, router, tmp_path, state=state)
    result = fetcher.fetch(only(registry))

    assert result.etag == 'W/"new"'
    stored = state.get("sudo:ics")
    assert stored.etag == 'W/"new"'
    assert stored.last_modified == "Wed, 05 Aug 2026 01:00:00 GMT"
    assert stored.last_content_hash == result.content_hash


def test_no_conditional_headers_without_stored_state(tmp_path):
    registry = make_registry(space("sudo", {"adapter": "ics", "url": "https://sudoroom.org/c.ics", "label": "ics"}))
    router = Router()
    router.add("https://sudoroom.org/c.ics", reply(body=ICS_BODY, content_type="text/calendar"))

    fetcher, _ = make_fetcher(registry, router, tmp_path)
    result = fetcher.fetch(only(registry))

    assert "if-none-match" not in router.target_requests[0].headers
    assert result.conditional is False


# ------------------------------------------------------- HTTP 200 is not success


def test_a_404_with_a_valid_rss_body_keeps_both_the_status_and_the_body(tmp_path):
    """Live in this registry: 404 carrying a populated, valid RSS feed."""
    registry = make_registry(space("boxshop", {"adapter": "rss", "url": "https://www.lower48.org/feed", "label": "rss"}))
    router = Router()
    router.add(
        "https://www.lower48.org/feed",
        reply(status=404, body=RSS_BODY, content_type="application/rss+xml"),
    )

    fetcher, _ = make_fetcher(registry, router, tmp_path)
    result = fetcher.fetch(only(registry))

    # Four separate facts, none of them collapsed and none of them pre-judged.
    assert result.status_code == 404
    assert result.content_type == "application/rss+xml"
    assert result.body == RSS_BODY
    assert result.byte_count == len(RSS_BODY)

    # The transport verdict says we got an answer; the adapter decides the rest.
    assert result.outcome is Outcome.FETCHED
    assert result.failed is False
    assert result.content_type_is("application/rss+xml")
    assert result.expect_content_type("application/rss+xml") == "application/rss+xml"
    assert result.raw_path is not None and result.raw_path.exists()


def test_a_404_with_an_rss_content_type_over_an_html_body_reports_both(tmp_path):
    """The 1.35 MB case: the header says RSS, the bytes are HTML. Both survive."""
    html = b"<!doctype html>" + b"<p>filler</p>" * 500
    registry = make_registry(space("dojo", {"adapter": "rss", "url": "https://events.hackerdojo.com/feed", "label": "rss"}))
    router = Router()
    router.add(
        "https://events.hackerdojo.com/feed",
        reply(status=404, body=html, content_type="application/rss+xml"),
    )

    fetcher, _ = make_fetcher(registry, router, tmp_path)
    result = fetcher.fetch(only(registry))

    assert result.status_code == 404
    assert result.content_type == "application/rss+xml"
    assert result.byte_count == len(html)
    assert result.body.startswith(b"<!doctype html>")
    # The content type alone would have fooled us; the adapter needs the bytes.
    assert result.expect_content_type("application/rss+xml")


def test_a_200_with_the_wrong_content_type_is_reported_faithfully(tmp_path):
    """``?format=ical`` answering 200 with ``text/html``."""
    registry = make_registry(
        space("boxshop", {"adapter": "ics", "url": "https://www.lower48.org/events?format=ical", "label": "ical"})
    )
    router = Router()
    router.add(
        "https://www.lower48.org/events?format=ical",
        reply(status=200, body=HOMEPAGE_BODY, content_type="text/html; charset=utf-8"),
    )

    fetcher, _ = make_fetcher(registry, router, tmp_path)
    result = fetcher.fetch(only(registry))

    assert result.outcome is Outcome.FETCHED
    assert result.status_code == 200
    assert result.content_type == "text/html"
    assert result.charset == "utf-8"
    assert result.content_type_header == "text/html; charset=utf-8"
    assert result.byte_count == len(HOMEPAGE_BODY)

    # The layer does not raise. The adapter asks, and then decides.
    with pytest.raises(ContentTypeMismatch) as excinfo:
        result.expect_content_type("text/calendar")
    assert "text/html" in str(excinfo.value)
    assert "200" in str(excinfo.value)


def test_a_byte_identical_homepage_is_detectable_by_hash(tmp_path):
    """``?ical=1`` returning a copy of the homepage, 200 and all."""
    registry = make_registry(
        space(
            "space",
            {"adapter": "ics", "url": "https://space.example.org/?ical=1", "label": "ical"},
            {"adapter": "llm_html", "url": "https://space.example.org/", "label": "home"},
        )
    )
    router = Router()
    router.add("https://space.example.org/?ical=1", reply(body=HOMEPAGE_BODY, content_type="text/html"))
    router.add("https://space.example.org/", reply(body=HOMEPAGE_BODY, content_type="text/html"))

    fetcher, _ = make_fetcher(registry, router, tmp_path)
    feed, home = list(fetcher.fetch_all())

    assert feed.status_code == home.status_code == 200
    assert feed.content_hash == home.content_hash
    assert not feed.content_type_is("text/calendar")


def test_content_type_matching_rules():
    def result(content_type: str | None) -> FetchResult:
        return FetchResult(
            space_id="s", label="l", adapter="rss", url="https://x/", outcome=Outcome.FETCHED,
            content_type=content_type,
        )

    assert result("application/rss+xml").content_type_is("application/xml")
    assert result("application/rss+xml").content_type_is("application/rss+xml")
    assert result("text/calendar").content_type_is("text/*")
    assert not result("text/html").content_type_is("text/calendar")
    assert not result(None).content_type_is("text/calendar")
    assert result(None).expect_content_type("text/calendar", allow_missing=True) == ""
    with pytest.raises(ContentTypeMismatch):
        result(None).expect_content_type("text/calendar")
    with pytest.raises(ValueError):
        result("text/html").expect_content_type()


def test_split_content_type():
    assert split_content_type("text/calendar; charset=UTF-8") == ("text/calendar", "utf-8")
    assert split_content_type("APPLICATION/JSON") == ("application/json", None)
    assert split_content_type(None) == (None, None)


def test_text_decodes_with_the_declared_charset(tmp_path):
    body = "Café Night".encode("latin-1")
    registry = make_registry(space("s", {"adapter": "rss", "url": "https://s.example.org/f", "label": "f"}))
    router = Router()
    router.add("https://s.example.org/f", reply(body=body, content_type="text/xml; charset=iso-8859-1"))

    fetcher, _ = make_fetcher(registry, router, tmp_path)
    result = fetcher.fetch(only(registry))

    assert result.text == "Café Night"


# --------------------------------------------------------------------------- raw/


def test_raw_archiving_writes_the_expected_path(tmp_path):
    registry = make_registry(
        space("ace", {"adapter": "ics", "url": "https://www.acemakerspace.org/calendar/list/?ical=1", "label": "tribe-ics-list"})
    )
    router = Router()
    router.add(
        "https://www.acemakerspace.org/calendar/list/?ical=1",
        reply(body=ICS_BODY, content_type="text/calendar; charset=UTF-8"),
    )

    fetcher, _ = make_fetcher(registry, router, tmp_path)
    result = fetcher.fetch(only(registry))

    expected = tmp_path / "raw" / "2026-08-05" / "ace-tribe-ics-list.ics"
    assert result.raw_path == expected
    assert expected.read_bytes() == ICS_BODY, "written verbatim"


def test_the_raw_extension_follows_the_response_not_the_adapter(tmp_path):
    """A .html file next to an `ics` adapter is the bug, visible in a listing."""
    registry = make_registry(
        space("boxshop", {"adapter": "ics", "url": "https://www.lower48.org/e?format=ical", "label": "ical"})
    )
    router = Router()
    router.add("https://www.lower48.org/e?format=ical", reply(body=HOMEPAGE_BODY, content_type="text/html"))

    fetcher, _ = make_fetcher(registry, router, tmp_path)
    result = fetcher.fetch(only(registry))

    assert result.raw_path.name == "boxshop-ical.html"


def test_raw_is_archived_before_the_adapter_sees_anything_even_on_a_404(tmp_path):
    registry = make_registry(space("boxshop", {"adapter": "rss", "url": "https://www.lower48.org/feed", "label": "rss"}))
    router = Router()
    router.add("https://www.lower48.org/feed", reply(status=404, body=RSS_BODY, content_type="application/rss+xml"))

    fetcher, _ = make_fetcher(registry, router, tmp_path)
    result = fetcher.fetch(only(registry))

    assert result.raw_path.read_bytes() == RSS_BODY
    assert result.raw_path.name == "boxshop-rss.xml"


def test_raw_is_never_mutated(tmp_path):
    """A second run the same day with different bytes writes a sibling."""
    day = tmp_path / "raw" / "2026-08-05"
    first = archive_raw(
        b"one", space_id="ace", label="ics", adapter="ics", content_type="text/calendar",
        raw_dir=tmp_path / "raw", day=FIXED_NOW.date(), now=FIXED_NOW,
    )
    second = archive_raw(
        b"two", space_id="ace", label="ics", adapter="ics", content_type="text/calendar",
        raw_dir=tmp_path / "raw", day=FIXED_NOW.date(), now=FIXED_NOW,
    )

    assert first != second
    assert first.read_bytes() == b"one", "the original must not be touched"
    assert second.read_bytes() == b"two"
    assert sorted(p.name for p in day.iterdir()) == ["ace-ics-031500.ics", "ace-ics.ics"]


def test_re_archiving_identical_bytes_reuses_the_existing_file(tmp_path):
    first = archive_raw(
        b"same", space_id="ace", label="ics", adapter="ics", content_type="text/calendar",
        raw_dir=tmp_path / "raw", day=FIXED_NOW.date(), now=FIXED_NOW,
    )
    second = archive_raw(
        b"same", space_id="ace", label="ics", adapter="ics", content_type="text/calendar",
        raw_dir=tmp_path / "raw", day=FIXED_NOW.date(), now=FIXED_NOW,
    )
    assert first == second
    assert len(list((tmp_path / "raw" / "2026-08-05").iterdir())) == 1


def test_archive_extension_falls_back_to_the_adapter_when_the_server_says_nothing():
    assert archive_extension(None, "ics") == "ics"
    assert archive_extension(None, "tribe_rest") == "json"
    assert archive_extension(None, "bookwhen_html") == "html"
    assert archive_extension("application/vnd.thing+json", "rss") == "json"
    assert archive_extension("application/atom+xml", "rss") == "xml"
    assert archive_extension(None, "unknown_adapter") == "bin"


def test_archiving_can_be_disabled_for_dry_runs(tmp_path):
    registry = make_registry(space("s", {"adapter": "ics", "url": "https://s.example.org/f.ics", "label": "f"}))
    router = Router()
    router.add("https://s.example.org/f.ics", reply(body=ICS_BODY, content_type="text/calendar"))

    fetcher, _ = make_fetcher(registry, router, tmp_path, archive=False)
    result = fetcher.fetch(only(registry))

    assert result.body == ICS_BODY
    assert result.raw_path is None
    assert not (tmp_path / "raw").exists()


# --------------------------------------------------------------------------- retries


def test_retry_then_fail_marks_the_source_failed(tmp_path):
    registry = make_registry(space("dojo", {"adapter": "ics", "url": "https://events.hackerdojo.com/f.ics", "label": "ics"}))
    router = Router()
    router.add(
        "https://events.hackerdojo.com/f.ics",
        boom(httpx.ConnectError("connection reset")),
    )
    state = InMemoryStateStore()

    fetcher, clock = make_fetcher(registry, router, tmp_path, state=state)
    result = fetcher.fetch(only(registry))

    assert result.outcome is Outcome.FAILED
    assert result.failed is True
    assert result.attempts == 3, "one attempt plus two retries"
    assert result.status_code is None and result.body is None
    assert "ConnectError" in (result.error or "")
    assert len(router.target_requests) == 3
    # 2 s rate limit after robots.txt, then backoff 1 s (which pays for 1 s of
    # the next 2 s rate-limit gap), then backoff 2 s (which covers the gap
    # entirely). Backoff and rate limiting compose rather than stacking.
    assert clock.sleeps == [2.0, 1.0, 1.0, 2.0]
    assert clock.total_slept == 6.0
    assert state.get("dojo:ics").consecutive_failures == 1


def test_consecutive_failures_accumulate_across_runs(tmp_path):
    registry = make_registry(space("dojo", {"adapter": "ics", "url": "https://events.hackerdojo.com/f.ics", "label": "ics"}))
    router = Router()
    router.add("https://events.hackerdojo.com/f.ics", boom(httpx.ConnectTimeout("timed out")))
    state = InMemoryStateStore()

    fetcher, _ = make_fetcher(registry, router, tmp_path, state=state)
    fetcher.fetch(only(registry))
    fetcher.fetch(only(registry))

    assert state.get("dojo:ics").consecutive_failures == 2


def test_a_transient_error_recovers_on_retry(tmp_path):
    registry = make_registry(space("s", {"adapter": "ics", "url": "https://s.example.org/f.ics", "label": "f"}))
    router = Router()
    router.add(
        "https://s.example.org/f.ics",
        reply(status=503),
        reply(status=503),
        reply(body=ICS_BODY, content_type="text/calendar"),
    )

    fetcher, _ = make_fetcher(registry, router, tmp_path)
    result = fetcher.fetch(only(registry))

    assert result.outcome is Outcome.FETCHED
    assert result.status_code == 200
    assert result.attempts == 3
    assert result.body == ICS_BODY


def test_a_persistent_503_is_failed_but_still_reports_status_and_body(tmp_path):
    registry = make_registry(space("s", {"adapter": "ics", "url": "https://s.example.org/f.ics", "label": "f"}))
    router = Router()
    router.add("https://s.example.org/f.ics", reply(status=503, body=b"maintenance", content_type="text/html"))

    fetcher, _ = make_fetcher(registry, router, tmp_path)
    result = fetcher.fetch(only(registry))

    assert result.outcome is Outcome.FAILED
    assert result.status_code == 503
    assert result.body == b"maintenance"
    assert result.byte_count == len(b"maintenance")
    assert result.attempts == 3


def test_a_404_is_not_retried(tmp_path):
    """404 carries a real feed here; retrying it would be useless and rude."""
    registry = make_registry(space("boxshop", {"adapter": "rss", "url": "https://www.lower48.org/feed", "label": "rss"}))
    router = Router()
    router.add("https://www.lower48.org/feed", reply(status=404, body=RSS_BODY, content_type="application/rss+xml"))

    fetcher, _ = make_fetcher(registry, router, tmp_path)
    result = fetcher.fetch(only(registry))

    assert result.attempts == 1
    assert len(router.target_requests) == 1
    assert 404 not in RETRY_STATUS_CODES


def test_cloudflare_525_is_retried(tmp_path):
    """events.hackerdojo.com has answered 525 for over a year."""
    registry = make_registry(space("dojo", {"adapter": "ics", "url": "https://events.hackerdojo.com/f.ics", "label": "ics"}))
    router = Router()
    router.add("https://events.hackerdojo.com/f.ics", reply(status=525))

    fetcher, _ = make_fetcher(registry, router, tmp_path)
    result = fetcher.fetch(only(registry))

    assert result.attempts == 3
    assert result.outcome is Outcome.FAILED
    assert result.status_code == 525


# --------------------------------------------------------------------------- skipping


def test_a_todo_url_is_skipped_without_a_request(tmp_path):
    registry = make_registry(
        space("sequoia", {"adapter": "bookwhen_html", "url": "TODO", "label": "bookwhen-public"})
    )
    router = Router()

    fetcher, _ = make_fetcher(registry, router, tmp_path)
    result = fetcher.fetch(only(registry))

    assert result.outcome is Outcome.SKIPPED
    assert result.skipped is True
    assert "TODO" in (result.reason or "")
    assert router.requests == []


def test_a_disabled_source_is_not_in_the_run_set_and_is_skipped_if_asked(tmp_path):
    registry = make_registry(
        space(
            "noisebridge",
            {"adapter": "ics", "url": "https://n.example.org/a.ics", "label": "a"},
            {"adapter": "ics", "url": "https://n.example.org/b.ics", "label": "b", "enabled": False},
        )
    )
    router = Router()
    router.add("https://n.example.org/a.ics", reply(body=ICS_BODY, content_type="text/calendar"))

    fetcher, _ = make_fetcher(registry, router, tmp_path)
    results = list(fetcher.fetch_all())
    assert [r.label for r in results] == ["a"]

    disabled = registry.skipped_sources[0]
    assert fetcher.fetch(disabled).outcome is Outcome.SKIPPED


# --------------------------------------------------------------------------- urls


def test_gcal_ics_urls_are_built_from_the_calendar_id():
    from pipeline.config import Source

    source = Source(
        adapter="gcal_ics",
        calendar_id="c_69d095@group.calendar.google.com",
        label="community-calendar",
    )
    assert resolve_url(source) == (
        "https://calendar.google.com/calendar/ical/"
        "c_69d095%40group.calendar.google.com/public/basic.ics"
    )


def test_registry_params_are_merged_into_the_query(tmp_path):
    """The Crucible loses 72% of its catalog without ``orderby=date``."""
    registry = make_registry(
        space(
            "crucible",
            {
                "adapter": "json",
                "url": "https://www.thecrucible.org/wp-json/wc/store/v1/products",
                "params": {"per_page": 100, "orderby": "date"},
                # Required for this adapter since issue 0022: the two registered
                # documents have nothing in common, so the shape is named rather
                # than sniffed.
                "shape": "woocommerce_store_products",
                "label": "woocommerce-store-api",
            },
        )
    )
    router = Router()
    router.add(
        "https://www.thecrucible.org/wp-json/wc/store/v1/products?per_page=100&orderby=date",
        reply(body=b"[]", content_type="application/json"),
    )

    fetcher, _ = make_fetcher(registry, router, tmp_path)
    result = fetcher.fetch(only(registry))

    assert result.outcome is Outcome.FETCHED
    assert "orderby=date" in router.target_urls[0]
    assert str(request_url(only(registry).source)).endswith("per_page=100&orderby=date")


def test_a_paginating_adapter_can_fetch_follow_up_pages(tmp_path):
    registry = make_registry(
        space("ace", {"adapter": "tribe_rest", "url": "https://www.acemakerspace.org/wp-json/tribe/events/v1/events", "label": "tribe-rest"})
    )
    router = Router()
    router.add(
        "https://www.acemakerspace.org/wp-json/tribe/events/v1/events",
        reply(body=b'{"page":1}', content_type="application/json"),
    )
    router.add(
        "https://www.acemakerspace.org/wp-json/tribe/events/v1/events?page=2",
        reply(body=b'{"page":2}', content_type="application/json"),
    )

    ref = only(registry)
    fetcher, clock = make_fetcher(registry, router, tmp_path)
    first = fetcher.fetch(ref)
    second = fetcher.fetch_url(
        ref,
        "https://www.acemakerspace.org/wp-json/tribe/events/v1/events?page=2",
        label_suffix="page2",
    )

    assert first.body == b'{"page":1}' and second.body == b'{"page":2}'
    assert second.label == "tribe-rest-page2"
    assert second.raw_path.name == "ace-tribe-rest-page2.json"
    # Page 2 queued behind page 1 on the same host, and robots was not re-read.
    assert clock.sleeps == [2.0, 2.0]
    assert len(router.robots_requests) == 1


def test_redirects_are_reported(tmp_path):
    registry = make_registry(space("s", {"adapter": "ics", "url": "https://s.example.org/old.ics", "label": "f"}))
    router = Router()
    router.add(
        "https://s.example.org/old.ics",
        reply(status=301, headers={"Location": "https://s.example.org/new.ics"}),
    )
    router.add("https://s.example.org/new.ics", reply(body=ICS_BODY, content_type="text/calendar"))

    fetcher, _ = make_fetcher(registry, router, tmp_path)
    result = fetcher.fetch(only(registry))

    assert result.outcome is Outcome.FETCHED
    assert result.redirected is True
    assert result.final_url == "https://s.example.org/new.ics"
    assert result.url == "https://s.example.org/old.ics"


# --------------------------------------------------------------------------- state store


def test_in_memory_store_satisfies_the_protocol_and_round_trips():
    store = InMemoryStateStore()
    assert isinstance(store, HttpStateStore)
    assert store.get("missing") is None

    store.put("ace:tribe-rest", SourceState(etag='W/"1"', consecutive_failures=2))
    got = store.get("ace:tribe-rest")
    assert got.etag == 'W/"1"'
    assert got.consecutive_failures == 2

    # Copies out, so a caller mutating a result cannot corrupt the store.
    got.etag = "mutated"
    assert store.get("ace:tribe-rest").etag == 'W/"1"'


def test_source_key_is_space_id_and_label():
    registry = make_registry(
        space("ace", {"adapter": "ics", "url": "https://a.example.org/f.ics", "label": "tribe-ics-list"}),
    )
    assert source_key(only(registry)) == "ace:tribe-ics-list"


def test_source_key_falls_back_to_the_adapter_name():
    registry = make_registry(space("ace", {"adapter": "ics", "url": "https://a.example.org/f.ics"}))
    assert source_key(only(registry)) == "ace:ics"


# --------------------------------------------------------------------------- run set


def test_fetch_all_walks_the_real_registrys_run_set_without_network(tmp_path):
    """The whole run set, mocked end to end — spaces one at a time, in order."""
    registry = load_registry(SOURCES_YAML, env={CONTACT_ENV_VAR: TEST_CONTACT})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=ICS_BODY, headers={"Content-Type": "text/calendar"})

    clock = FakeClock()
    fetcher = Fetcher(
        registry,
        transport=httpx.MockTransport(handler),
        raw_dir=tmp_path / "raw",
        sleep=clock.sleep,
        clock=clock,
        now=lambda: FIXED_NOW,
    )
    results = list(fetcher.fetch_all())

    assert len(results) == len(registry.enabled_sources)
    outcomes = {r.outcome for r in results}
    assert outcomes <= {Outcome.FETCHED, Outcome.SKIPPED}
    assert any(r.outcome is Outcome.SKIPPED for r in results), "the TODO source is skipped"
    assert all(r.byte_count == len(ICS_BODY) for r in results if r.outcome is Outcome.FETCHED)
    # Nothing ran for free: the run spent real seconds being polite.
    assert clock.total_slept > 0
