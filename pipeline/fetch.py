"""HTTP layer: rate limiting, robots.txt, conditional GET, ``raw/`` archiving.

The only module that talks to the network. One ``httpx`` client with HTTP/2 and
redirects followed, shared for the whole run.

Responsibilities:

- Honor ``robots.txt`` per host (``urllib.robotparser``), cached per run. A
  disallow means we do not fetch it — not that we find an equivalent route.
  Path-level disallows bind us regardless of which group matched.
- Rate limit to 2 seconds per host (10 s for Ace) and honor ``Crawl-delay``.
  These are volunteer nonprofits on shared hosting.
- Conditional GET using the ``ETag`` / ``Last-Modified`` stored per source in
  SQLite; a 304 skips parsing entirely and just bumps ``last_seen``.
- 30 s timeout, 2 retries with backoff, then mark the source ``failed`` so
  ``normalize``/``dedupe`` carry forward the previous run's events.
- Archive every payload verbatim under ``raw/YYYY-MM-DD/``, write-once, kept 30
  days. Never mutate ``raw/`` — it is the evidence trail that answers "did the
  source change or did we?" in ten seconds.
- **HTTP 200 is not success.** Assert on content type *and* parse result, and
  treat disagreement as a failure. Observed live in this registry: ``?format=ical``
  returning 200 with ``text/html``; ``?ical=1`` returning a byte-identical copy
  of the homepage; and one endpoint returning 404 with a valid RSS body.
- Fetch only URLs named in ``sources.yaml``. No crawling, no link-following, no
  sitemap walking.

Implemented by issue 0006 (fetch layer).

The shape of the result is the design decision that matters
--------------------------------------------------------------------------

``FetchResult`` reports **status, content type, body and byte count as four
separate fields** and does not collapse them into a success boolean. This layer
must not pre-judge on the adapter's behalf, because in this registry, live:

- ``?format=ical`` returns 200 with ``text/html``
- ``?ical=1`` returns 200 with a byte-identical copy of the homepage
- a per-category feed returns 200 with the unfiltered full list
- one endpoint returns **404 with a valid, populated RSS body**
- another returns 404 carrying ``Content-Type: application/rss+xml`` over a
  1.35 MB HTML body

Only the adapter knows which of those is usable. ``FetchResult.outcome`` is a
*transport* verdict — did we get an answer, was it unchanged, were we forbidden,
did the connection die — and it deliberately says nothing about whether the
payload is the feed we wanted. ``expect_content_type()`` is offered so an
adapter can assert cheaply; it is never called for the adapter.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from pipeline.config import RAW_DIR, Registry, Source, SourceRef, Space

# --------------------------------------------------------------------------- knobs

#: Wall-clock ceiling for one request. Two sources here are 8 MB and 11 MB.
TIMEOUT_SECONDS = 30.0

#: Retries *after* the first attempt, so three attempts at worst.
MAX_RETRIES = 2

#: Backoff before retry N is ``BACKOFF_SECONDS * 2**N`` (1 s, then 2 s).
BACKOFF_SECONDS = 1.0

#: Statuses worth trying again. 404 is deliberately **not** here: one endpoint
#: in this registry answers 404 with a valid populated RSS body, and retrying it
#: would be both useless and rude. 525 is Cloudflare's TLS handshake failure,
#: which is what events.hackerdojo.com has returned for over a year.
RETRY_STATUS_CODES: frozenset[int] = frozenset(
    {408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524, 525, 526, 527}
)

#: Tokens that would mean we are pretending to be something we are not. The
#: robots.txt position in CLAUDE.md rests entirely on an honest User-Agent:
#: impersonating a browser or another crawler to land in a more permissive group
#: is evasion and voids the whole argument.
FORBIDDEN_USER_AGENT_TOKENS: tuple[str, ...] = (
    "mozilla",
    "applewebkit",
    "chrome",
    "safari",
    "gecko",
    "firefox",
    "edge/",
    "opera",
    "msie",
    "googlebot",
    "bingbot",
    "slurp",
    "duckduckbot",
    "baiduspider",
    "yandexbot",
    "claudebot",
    "claude-web",
    "anthropic-ai",
    "gptbot",
    "chatgpt-user",
    "ccbot",
    "curl/",
    "wget",
    "python-requests",
    "python-httpx",
)

#: Content types adapters commonly assert on. Exported so an adapter says
#: ``result.expect_content_type(*ICS_CONTENT_TYPES)`` rather than re-listing.
ICS_CONTENT_TYPES: tuple[str, ...] = ("text/calendar",)
JSON_CONTENT_TYPES: tuple[str, ...] = ("application/json", "text/json")
XML_CONTENT_TYPES: tuple[str, ...] = (
    "application/rss+xml",
    "application/atom+xml",
    "application/xml",
    "text/xml",
)
HTML_CONTENT_TYPES: tuple[str, ...] = ("text/html", "application/xhtml+xml")

#: File extension for the ``raw/`` archive, keyed by what the server *said* it
#: sent. Deriving the extension from the response rather than from the adapter
#: is intentional: it makes the ``?format=ical`` → ``text/html`` case visible in
#: a directory listing.
_EXT_BY_MIME: dict[str, str] = {
    "text/calendar": "ics",
    "application/calendar+json": "json",
    "application/json": "json",
    "text/json": "json",
    "application/rss+xml": "xml",
    "application/atom+xml": "xml",
    "application/xml": "xml",
    "text/xml": "xml",
    "text/html": "html",
    "application/xhtml+xml": "html",
    "text/plain": "txt",
    "text/csv": "csv",
}

#: Fallback when the server sends no Content-Type at all.
_EXT_BY_ADAPTER: dict[str, str] = {
    "ics": "ics",
    "gcal_ics": "ics",
    "tribe_rest": "json",
    "json": "json",
    "jsonld": "html",
    "nextdata": "html",
    "embedded_json": "html",
    "bookwhen_html": "html",
    "llm_html": "html",
    "rss": "xml",
}

_UNSAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

GCAL_ICS_TEMPLATE = "https://calendar.google.com/calendar/ical/{calendar_id}/public/basic.ics"


# --------------------------------------------------------------------------- errors


class FetchError(Exception):
    """Base class for every failure raised by this module."""


class DishonestUserAgentError(FetchError):
    """The configured User-Agent impersonates a browser or another crawler."""


class ContentTypeMismatch(FetchError):
    """The response Content-Type is not what the adapter asserted it must be.

    Raised only from :meth:`FetchResult.expect_content_type`, i.e. only when an
    adapter asks. The fetch layer itself never raises this — see the module
    docstring.
    """

    def __init__(self, result: FetchResult, expected: tuple[str, ...]) -> None:
        self.result = result
        self.expected = expected
        got = result.content_type or "<no Content-Type header>"
        super().__init__(
            f"{result.source_key}: expected {' or '.join(expected)}, got {got} "
            f"(HTTP {result.status_code}, {result.byte_count} bytes) from {result.url}"
        )


# --------------------------------------------------------------------------- state


@dataclass
class SourceState:
    """Per-source HTTP bookkeeping that survives between runs.

    Issue 0013 builds the SQLite table behind this; the field names are the
    column names it is expected to use. ``fetch.py`` only ever reads
    ``etag`` / ``last_modified`` and writes the rest.
    """

    etag: str | None = None
    last_modified: str | None = None
    last_status: int | None = None
    last_success_at: datetime | None = None
    last_seen: datetime | None = None
    consecutive_failures: int = 0
    last_content_hash: str | None = None


@runtime_checkable
class HttpStateStore(Protocol):
    """The storage interface the fetch layer needs, and nothing more.

    Issue 0013 drops a SQLite implementation in behind this without touching
    ``fetch.py``. Keys are :func:`source_key` values.
    """

    def get(self, key: str) -> SourceState | None:
        """Return the stored state for ``key``, or ``None`` if never fetched."""

    def put(self, key: str, state: SourceState) -> None:
        """Persist ``state`` under ``key``, replacing anything already there."""


class InMemoryStateStore:
    """A dict-backed :class:`HttpStateStore`. The default until issue 0013.

    Conditional GET degrades to unconditional GET across process restarts,
    which is correct-but-wasteful rather than wrong.
    """

    def __init__(self, initial: dict[str, SourceState] | None = None) -> None:
        self._states: dict[str, SourceState] = dict(initial or {})

    def get(self, key: str) -> SourceState | None:
        state = self._states.get(key)
        return replace(state) if state is not None else None

    def put(self, key: str, state: SourceState) -> None:
        self._states[key] = replace(state)

    def __len__(self) -> int:
        return len(self._states)

    def __contains__(self, key: object) -> bool:
        return key in self._states

    def keys(self) -> Iterable[str]:
        return self._states.keys()


# --------------------------------------------------------------------------- result


class Outcome(str, Enum):
    """What happened at the *transport* level. Not a verdict on the payload.

    ``FETCHED`` means we have an HTTP response — of any status — and its body.
    A 404 with a populated RSS body is ``FETCHED`` with ``status_code == 404``
    and the body intact, because deciding what to do about that is the
    adapter's job and not this layer's.
    """

    FETCHED = "fetched"
    NOT_MODIFIED = "not_modified"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True)
class FetchResult:
    """Everything an adapter needs to decide, kept deliberately un-collapsed."""

    space_id: str
    label: str
    adapter: str
    url: str
    outcome: Outcome

    # --- the four facts, always separate -------------------------------------
    status_code: int | None = None
    content_type: str | None = None  # bare mime type, lowercased, no parameters
    body: bytes | None = None
    byte_count: int = 0

    # --- context -------------------------------------------------------------
    charset: str | None = None
    content_type_header: str | None = None  # verbatim, parameters and all
    headers: dict[str, str] = field(default_factory=dict)
    final_url: str | None = None
    redirected: bool = False
    etag: str | None = None
    last_modified: str | None = None
    conditional: bool = False
    attempts: int = 0
    elapsed_seconds: float = 0.0
    rate_limit_delay_seconds: float = 0.0
    raw_path: Path | None = None
    reason: str | None = None
    error: str | None = None

    # -- identity --------------------------------------------------------------

    @property
    def source_key(self) -> str:
        """``{space_id}:{label}`` — the state-store key for this source."""
        return f"{self.space_id}:{self.label}"

    # -- transport verdicts ----------------------------------------------------

    @property
    def failed(self) -> bool:
        """Transport failure. Issue 0014 carries forward on exactly this."""
        return self.outcome is Outcome.FAILED

    @property
    def not_modified(self) -> bool:
        """A 304. There is no body and nothing to parse."""
        return self.outcome is Outcome.NOT_MODIFIED

    @property
    def blocked(self) -> bool:
        """``robots.txt`` said no. Never carry forward — we must not have it."""
        return self.outcome is Outcome.BLOCKED

    @property
    def skipped(self) -> bool:
        """Not attempted: a ``TODO`` url, or ``enabled: false``."""
        return self.outcome is Outcome.SKIPPED

    @property
    def has_body(self) -> bool:
        return self.body is not None and self.byte_count > 0

    # -- payload ---------------------------------------------------------------

    @property
    def text(self) -> str:
        """The body decoded with the declared charset, defaulting to UTF-8.

        ``errors="replace"`` on purpose: a mojibake byte in a 5000-VEVENT feed
        must not take the whole space out of the calendar.
        """
        if self.body is None:
            return ""
        return self.body.decode(self.charset or "utf-8", errors="replace")

    @property
    def content_hash(self) -> str | None:
        """SHA-256 of the body. Detects the byte-identical-homepage case."""
        if self.body is None:
            return None
        return hashlib.sha256(self.body).hexdigest()

    # -- assertions the adapter may choose to make -----------------------------

    def content_type_is(self, *expected: str) -> bool:
        """True if the declared type matches any of ``expected``.

        ``"text/*"`` style wildcards are accepted, and ``+json`` / ``+xml``
        structured suffixes match their base type (so ``application/rss+xml``
        satisfies ``application/xml``).
        """
        return _content_type_matches(self.content_type, expected)

    def expect_content_type(self, *expected: str, allow_missing: bool = False) -> str:
        """Assert the Content-Type, or raise :class:`ContentTypeMismatch`.

        Offered, not applied. "HTTP 200 is not success" means the *adapter*
        decides whether a mismatch is fatal: the RSS adapter (issue 0025) has a
        live source that answers 404 with a perfectly good feed, and it needs
        the freedom to parse it anyway.
        """
        if not expected:
            raise ValueError("expect_content_type() needs at least one type")
        if self.content_type is None and allow_missing:
            return ""
        if not self.content_type_is(*expected):
            raise ContentTypeMismatch(self, expected)
        return self.content_type or ""

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        bits = [self.source_key, self.outcome.value]
        if self.status_code is not None:
            bits.append(f"HTTP {self.status_code}")
        if self.content_type:
            bits.append(self.content_type)
        bits.append(f"{self.byte_count} bytes")
        return " ".join(bits)


# --------------------------------------------------------------------------- helpers


def source_key(ref: SourceRef) -> str:
    """The stable per-source identity used for state and for ``raw/`` names."""
    return f"{ref.space.id}:{source_label(ref.source)}"


def source_label(source: Source) -> str:
    """``label`` if the registry gave one, otherwise the adapter name."""
    return source.label or source.adapter


def resolve_url(source: Source) -> str:
    """The URL this source is fetched from.

    ``gcal_ics`` carries a ``calendar_id`` rather than a URL; everything else
    carries the URL verbatim. This is the *only* place a URL is constructed
    rather than read from the registry, and it is a fixed Google template — not
    a guess. "Never guess a feed URL."

    The construction itself lives in :mod:`pipeline.adapters.gcal_ics` (issue
    0008) and is imported lazily here, because that module imports this one. One
    implementation rather than two is deliberate: the id appears in this project
    both as ``…%40group.calendar.google.com`` and as ``…@group.calendar.google.com``,
    and encoding an already-encoded id gives ``%2540``, which is a valid request
    for a calendar that does not exist. A second copy of that rule would be a
    second chance to get it wrong.
    """
    if source.calendar_id:
        from pipeline.adapters.gcal_ics import calendar_ics_url

        return calendar_ics_url(source.calendar_id)
    return source.url or ""


def request_url(source: Source) -> httpx.URL:
    """The resolved URL with the registry's ``params`` merged in.

    ``params`` is load-bearing, not cosmetic: The Crucible's Store API returns
    HTTP 200 and valid JSON with 72% of the catalog missing when ``orderby=date``
    is absent.
    """
    url = httpx.URL(resolve_url(source))
    if source.params:
        url = url.copy_merge_params({key: str(value) for key, value in source.params.items()})
    return url


def host_of(url: str | httpx.URL) -> str:
    """The rate-limiting key: the bare hostname, lowercased, port dropped.

    Rate limiting is per **host**, not per space. Several spaces publish through
    ``api.lu.ma``, and 2 seconds each is not 2 seconds if we count them apart.
    """
    parsed = urlsplit(str(url))
    return (parsed.hostname or "").lower()


def origin_of(url: str | httpx.URL) -> str:
    """``scheme://host[:port]`` — the scope of one ``robots.txt``."""
    parsed = urlsplit(str(url))
    return f"{parsed.scheme}://{parsed.netloc}".lower()


def split_content_type(header: str | None) -> tuple[str | None, str | None]:
    """``"text/calendar; charset=UTF-8"`` → ``("text/calendar", "utf-8")``."""
    if not header:
        return None, None
    parts = header.split(";")
    mime = parts[0].strip().lower() or None
    charset: str | None = None
    for param in parts[1:]:
        name, _, value = param.partition("=")
        if name.strip().lower() == "charset":
            charset = value.strip().strip('"').lower() or None
    return mime, charset


def _content_type_matches(actual: str | None, expected: Iterable[str]) -> bool:
    if actual is None:
        return False
    actual = actual.strip().lower()
    base, _, suffix = actual.partition("+")
    normalized = {actual}
    if suffix:
        # application/rss+xml also satisfies application/xml.
        normalized.add(f"{base.split('/')[0]}/{suffix}")
        normalized.add(f"application/{suffix}")
    for want in expected:
        want = want.strip().lower()
        if want.endswith("/*"):
            if actual.startswith(want[:-1]):
                return True
        elif want in normalized:
            return True
    return False


def assert_honest_user_agent(user_agent: str) -> str:
    """Raise unless the User-Agent is ours and carries a contact URL.

    CLAUDE.md: "Never impersonate a browser or another crawler to obtain a more
    permissive group. That would be evasion and would void everything above."
    This is that sentence, enforced.
    """
    if not user_agent or not user_agent.strip():
        raise DishonestUserAgentError("the registry supplies no User-Agent")
    lowered = user_agent.lower()
    for token in FORBIDDEN_USER_AGENT_TOKENS:
        if token in lowered:
            raise DishonestUserAgentError(
                f"User-Agent {user_agent!r} contains {token!r}, which impersonates a "
                "browser or another crawler. The robots.txt position in CLAUDE.md "
                "depends on us being honestly identifiable; evasion voids it."
            )
    if "http://" not in lowered and "https://" not in lowered:
        raise DishonestUserAgentError(
            f"User-Agent {user_agent!r} carries no contact URL. A site operator "
            "must be able to reach us — that is the whole argument."
        )
    return user_agent


def archive_extension(content_type: str | None, adapter: str) -> str:
    """Extension for the ``raw/`` file, from what the server actually sent."""
    if content_type:
        if content_type in _EXT_BY_MIME:
            return _EXT_BY_MIME[content_type]
        if content_type.endswith("+json"):
            return "json"
        if content_type.endswith("+xml"):
            return "xml"
    return _EXT_BY_ADAPTER.get(adapter, "bin")


def _safe_component(value: str) -> str:
    cleaned = _UNSAFE_FILENAME_RE.sub("-", value).strip("-")
    return cleaned or "unnamed"


def archive_raw(
    body: bytes,
    *,
    space_id: str,
    label: str,
    adapter: str,
    content_type: str | None,
    raw_dir: Path,
    day: date,
    now: datetime | None = None,
) -> Path:
    """Write ``body`` verbatim to ``raw/YYYY-MM-DD/{space_id}-{label}.{ext}``.

    **Never mutates ``raw/``.** If the path already exists and holds identical
    bytes, the existing file is left alone and returned. If it exists and
    differs — a second run on the same day, which is exactly when the diff is
    interesting — a timestamped sibling is written instead of an overwrite.
    """
    day_dir = raw_dir / day.isoformat()
    day_dir.mkdir(parents=True, exist_ok=True)

    stem = f"{_safe_component(space_id)}-{_safe_component(label)}"
    ext = archive_extension(content_type, adapter)
    path = day_dir / f"{stem}.{ext}"

    if path.exists():
        if path.read_bytes() == body:
            return path
        stamp = (now or datetime.now()).strftime("%H%M%S")
        path = day_dir / f"{stem}-{stamp}.{ext}"
        counter = 2
        while path.exists():
            path = day_dir / f"{stem}-{stamp}-{counter}.{ext}"
            counter += 1

    path.write_bytes(body)
    return path


# --------------------------------------------------------------------------- rate limit


class RateLimiter:
    """Sleeps so that consecutive requests to one host are ``delay`` apart.

    Keyed by host rather than by space: ``api.lu.ma`` serves four spaces in this
    registry, and they must queue behind each other.
    """

    def __init__(
        self,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sleep = sleep
        self._clock = clock
        self._last_request: dict[str, float] = {}
        self.total_wait_seconds = 0.0

    def wait(self, host: str, delay: float) -> float:
        """Block until ``host`` may be hit again. Returns seconds slept."""
        now = self._clock()
        slept = 0.0
        last = self._last_request.get(host)
        if last is not None:
            remaining = delay - (now - last)
            if remaining > 0:
                self._sleep(remaining)
                slept = remaining
                now = self._clock()
        self._last_request[host] = now
        self.total_wait_seconds += slept
        return slept

    def last_request(self, host: str) -> float | None:
        return self._last_request.get(host)


# --------------------------------------------------------------------------- robots


@dataclass(frozen=True)
class RobotsRules:
    """One host's ``robots.txt``, parsed. Cached for the whole run."""

    origin: str
    status_code: int | None
    parser: RobotFileParser | None
    unreachable: bool = False
    error: str | None = None

    def allows(self, url: str, user_agent: str) -> bool:
        """Whether we may fetch ``url``.

        Checked against **both** our own product token and the ``*`` group. RFC
        9309 says a crawler obeys exactly one group, and by that rule we follow
        ``*`` — we name ourselves ``bayarea-maker-calendar`` and match no
        enumerated bot group. Consulting ``*`` as well is deliberately stricter
        than the RFC: CLAUDE.md's "path-level disallows bind us regardless of
        which group we match" is the commitment, and lower48.org's
        ``?format=ical`` disallow is the live case.
        """
        if self.unreachable:
            return False
        if self.parser is None:
            return True
        return bool(self.parser.can_fetch(user_agent, url)) and bool(
            self.parser.can_fetch("*", url)
        )

    def crawl_delay(self, user_agent: str) -> float | None:
        """The largest ``Crawl-delay`` that could apply to us, if any."""
        if self.parser is None:
            return None
        delays: list[float] = []
        for agent in (user_agent, "*"):
            try:
                value = self.parser.crawl_delay(agent)
            except (AttributeError, ValueError):  # pragma: no cover - defensive
                value = None
            if value is not None:
                try:
                    delays.append(float(value))
                except (TypeError, ValueError):  # pragma: no cover - defensive
                    continue
        return max(delays) if delays else None


class RobotsCache:
    """Fetches and caches one ``robots.txt`` per origin, per run."""

    def __init__(
        self,
        client: httpx.Client,
        *,
        user_agent: str,
        limiter: RateLimiter | None = None,
        timeout: float = TIMEOUT_SECONDS,
    ) -> None:
        self._client = client
        self._user_agent = user_agent
        self._limiter = limiter
        self._timeout = timeout
        self._cache: dict[str, RobotsRules] = {}
        self.fetch_count = 0

    def rules_for(self, url: str | httpx.URL, *, delay: float = 0.0) -> RobotsRules:
        origin = origin_of(url)
        cached = self._cache.get(origin)
        if cached is not None:
            return cached
        rules = self._load(origin, delay=delay)
        self._cache[origin] = rules
        return rules

    def _load(self, origin: str, *, delay: float) -> RobotsRules:
        robots_url = f"{origin}/robots.txt"
        if self._limiter is not None and delay > 0:
            self._limiter.wait(host_of(origin), delay)
        self.fetch_count += 1
        try:
            response = self._client.get(
                robots_url,
                headers={"User-Agent": self._user_agent, "Accept": "text/plain,*/*"},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            # RFC 9309 §2.3.1.4: if robots.txt is unreachable, assume disallow.
            # Reported as FAILED rather than BLOCKED by the caller, so issue
            # 0014 carries the space forward instead of dropping it.
            return RobotsRules(origin, None, None, unreachable=True, error=str(exc))

        status = response.status_code
        if status == 200:
            parser = RobotFileParser()
            parser.parse(response.text.splitlines())
            return RobotsRules(origin, status, parser)
        if 400 <= status < 500:
            # No robots.txt (or forbidden): everything is allowed. This is the
            # documented behavior for 404 and the pragmatic one for 401/403.
            return RobotsRules(origin, status, None)
        return RobotsRules(
            origin,
            status,
            None,
            unreachable=True,
            error=f"robots.txt returned HTTP {status}",
        )


# --------------------------------------------------------------------------- fetcher


class Fetcher:
    """The run's single HTTP client, with all the good-citizen rules attached.

    Construct one per run and reuse it: the rate limiter, the ``robots.txt``
    cache and the HTTP/2 connection pool are all per-instance.
    """

    def __init__(
        self,
        registry: Registry,
        *,
        client: httpx.Client | None = None,
        transport: httpx.BaseTransport | None = None,
        state: HttpStateStore | None = None,
        raw_dir: Path | str = RAW_DIR,
        archive: bool = True,
        obey_robots: bool = True,
        conditional: bool = True,
        max_retries: int = MAX_RETRIES,
        backoff_seconds: float = BACKOFF_SECONDS,
        timeout: float = TIMEOUT_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.registry = registry
        self.user_agent = assert_honest_user_agent(registry.user_agent)
        self.state: HttpStateStore = state if state is not None else InMemoryStateStore()
        self.raw_dir = Path(raw_dir)
        self.archive = archive
        self.obey_robots = obey_robots
        self.conditional = conditional
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.timeout = timeout

        self._sleep = sleep
        self._clock = clock
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._owns_client = client is None

        self.limiter = RateLimiter(sleep=sleep, clock=clock)
        self._client = client or httpx.Client(
            http2=True,
            follow_redirects=True,
            timeout=httpx.Timeout(timeout),
            headers={
                "User-Agent": self.user_agent,
                "Accept-Encoding": "gzip, deflate",
            },
            transport=transport,
        )
        self.robots = RobotsCache(
            self._client,
            user_agent=self.user_agent,
            limiter=self.limiter,
            timeout=timeout,
        )

    # -- lifecycle -------------------------------------------------------------

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> Fetcher:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- public API ------------------------------------------------------------

    def fetch_all(self) -> Iterator[FetchResult]:
        """Fetch the run set in registry order, spaces one at a time."""
        for ref in self.registry.iter_enabled():
            yield self.fetch(ref)

    def fetch(self, ref: SourceRef) -> FetchResult:
        """Fetch one registered source, honoring every rule in this module."""
        space, source = ref
        label = source_label(source)

        if not source.enabled:
            return self._skip(space, source, label, "source is disabled in the registry")
        if source.is_todo:
            return self._skip(
                space, source, label, "url is still the TODO placeholder (issue 0002)"
            )

        url = request_url(source)
        return self._fetch_url(space, source, label, url, conditional=self.conditional)

    def fetch_url(
        self,
        ref: SourceRef,
        url: str | httpx.URL,
        *,
        label_suffix: str | None = None,
        conditional: bool = False,
    ) -> FetchResult:
        """Fetch a follow-up URL for an already-registered source.

        This is how a paginating adapter walks ``next_rest_url`` or
        ``X-WP-TotalPages`` while staying inside the same rate limiter and the
        same ``robots.txt`` decision. It is **not** a general fetcher: the
        caller must have derived the URL from a source in ``sources.yaml``.
        Conditional GET defaults off, since page 2 has no stored validator.
        """
        space, source = ref
        label = source_label(source)
        if label_suffix:
            label = f"{label}-{label_suffix}"
        return self._fetch_url(space, source, label, httpx.URL(str(url)), conditional=conditional)

    # -- internals -------------------------------------------------------------

    def _skip(self, space: Space, source: Source, label: str, reason: str) -> FetchResult:
        return FetchResult(
            space_id=space.id,
            label=label,
            adapter=source.adapter,
            url=str(request_url(source)) if source.target else "",
            outcome=Outcome.SKIPPED,
            reason=reason,
        )

    def _delay_for(self, space: Space, rules: RobotsRules | None) -> float:
        """The larger of the registry's rate limit and the host's Crawl-delay."""
        delay = space.effective_rate_limit_seconds
        if rules is not None:
            crawl_delay = rules.crawl_delay(self.user_agent)
            if crawl_delay is not None:
                delay = max(delay, crawl_delay)
        return delay

    def _fetch_url(
        self,
        space: Space,
        source: Source,
        label: str,
        url: httpx.URL,
        *,
        conditional: bool,
    ) -> FetchResult:
        key = f"{space.id}:{label}"
        host = host_of(url)
        base_delay = space.effective_rate_limit_seconds

        # --- robots.txt, once per origin per run -----------------------------
        rules: RobotsRules | None = None
        if self.obey_robots:
            rules = self.robots.rules_for(url, delay=base_delay)
            if rules.unreachable:
                return self._record_failure(
                    space,
                    source,
                    label,
                    url,
                    reason="robots.txt could not be read",
                    error=rules.error or "robots.txt unreachable",
                )
            if not rules.allows(str(url), self.user_agent):
                # A disallow means we do not fetch it — not that we find an
                # equivalent route. Deliberately not a failure: there is nothing
                # to carry forward, because we are not permitted to have it.
                return FetchResult(
                    space_id=space.id,
                    label=label,
                    adapter=source.adapter,
                    url=str(url),
                    outcome=Outcome.BLOCKED,
                    reason=f"robots.txt at {rules.origin} disallows this path",
                )

        delay = self._delay_for(space, rules)

        # --- conditional GET --------------------------------------------------
        stored = self.state.get(key)
        headers: dict[str, str] = {}
        if conditional and stored is not None:
            if stored.etag:
                headers["If-None-Match"] = stored.etag
            if stored.last_modified:
                headers["If-Modified-Since"] = stored.last_modified
        is_conditional = bool(headers)

        # --- request, with retries -------------------------------------------
        waited = 0.0
        attempts = 0
        last_error: str | None = None
        response: httpx.Response | None = None
        started = self._clock()

        for attempt in range(self.max_retries + 1):
            waited += self.limiter.wait(host, delay)
            attempts = attempt + 1
            try:
                response = self._client.get(str(url), headers=headers)
            except httpx.HTTPError as exc:
                response = None
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code not in RETRY_STATUS_CODES:
                    break
                last_error = f"HTTP {response.status_code}"
            if attempt < self.max_retries:
                self._sleep(self.backoff_seconds * (2**attempt))

        elapsed = self._clock() - started

        if response is None:
            return self._record_failure(
                space,
                source,
                label,
                url,
                reason="request failed after retries",
                error=last_error,
                attempts=attempts,
                elapsed=elapsed,
                waited=waited,
                conditional=is_conditional,
            )

        mime, charset = split_content_type(response.headers.get("Content-Type"))
        common = {
            "space_id": space.id,
            "label": label,
            "adapter": source.adapter,
            "url": str(url),
            "status_code": response.status_code,
            "content_type": mime,
            "charset": charset,
            "content_type_header": response.headers.get("Content-Type"),
            "headers": {k.lower(): v for k, v in response.headers.items()},
            "final_url": str(response.url),
            "redirected": str(response.url) != str(url),
            "etag": response.headers.get("ETag"),
            "last_modified": response.headers.get("Last-Modified"),
            "conditional": is_conditional,
            "attempts": attempts,
            "elapsed_seconds": elapsed,
            "rate_limit_delay_seconds": waited,
        }

        # --- 304: nothing to parse, nothing to archive ------------------------
        if response.status_code == 304:
            self._save_state(
                key,
                stored,
                status=304,
                success=True,
                content_hash=stored.last_content_hash if stored else None,
                etag=response.headers.get("ETag") or (stored.etag if stored else None),
                last_modified=(
                    response.headers.get("Last-Modified")
                    or (stored.last_modified if stored else None)
                ),
            )
            return FetchResult(
                outcome=Outcome.NOT_MODIFIED,
                body=None,
                byte_count=0,
                reason="server reports the source unchanged since the last run",
                **common,
            )

        body = response.content
        raw_path: Path | None = None
        if self.archive and body:
            # Before parsing, always. raw/ is the evidence trail, and it is
            # written for the wrong-content-type responses too — those are
            # precisely the ones worth having on disk tomorrow.
            raw_path = archive_raw(
                body,
                space_id=space.id,
                label=label,
                adapter=source.adapter,
                content_type=mime,
                raw_dir=self.raw_dir,
                day=self._now().date(),
                now=self._now(),
            )

        retryable = response.status_code in RETRY_STATUS_CODES
        outcome = Outcome.FAILED if retryable else Outcome.FETCHED

        self._save_state(
            key,
            stored,
            status=response.status_code,
            success=not retryable,
            content_hash=hashlib.sha256(body).hexdigest() if body else None,
            etag=response.headers.get("ETag"),
            last_modified=response.headers.get("Last-Modified"),
        )

        return FetchResult(
            outcome=outcome,
            body=body,
            byte_count=len(body),
            raw_path=raw_path,
            reason=(
                f"server error persisted after {attempts} attempts" if retryable else None
            ),
            error=last_error if retryable else None,
            **common,
        )

    def _record_failure(
        self,
        space: Space,
        source: Source,
        label: str,
        url: httpx.URL,
        *,
        reason: str,
        error: str | None,
        attempts: int = 0,
        elapsed: float = 0.0,
        waited: float = 0.0,
        conditional: bool = False,
    ) -> FetchResult:
        key = f"{space.id}:{label}"
        self._save_state(key, self.state.get(key), status=None, success=False)
        return FetchResult(
            space_id=space.id,
            label=label,
            adapter=source.adapter,
            url=str(url),
            outcome=Outcome.FAILED,
            attempts=attempts,
            elapsed_seconds=elapsed,
            rate_limit_delay_seconds=waited,
            conditional=conditional,
            reason=reason,
            error=error,
        )

    def _save_state(
        self,
        key: str,
        stored: SourceState | None,
        *,
        status: int | None,
        success: bool,
        content_hash: str | None = None,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> None:
        state = stored or SourceState()
        now = self._now()
        state.last_status = status
        state.last_seen = now
        if success:
            state.last_success_at = now
            state.consecutive_failures = 0
            if etag is not None:
                state.etag = etag
            if last_modified is not None:
                state.last_modified = last_modified
            if content_hash is not None:
                state.last_content_hash = content_hash
        else:
            state.consecutive_failures += 1
        self.state.put(key, state)


__all__ = [
    "BACKOFF_SECONDS",
    "FORBIDDEN_USER_AGENT_TOKENS",
    "GCAL_ICS_TEMPLATE",
    "HTML_CONTENT_TYPES",
    "ICS_CONTENT_TYPES",
    "JSON_CONTENT_TYPES",
    "MAX_RETRIES",
    "RETRY_STATUS_CODES",
    "TIMEOUT_SECONDS",
    "XML_CONTENT_TYPES",
    "ContentTypeMismatch",
    "DishonestUserAgentError",
    "FetchError",
    "FetchResult",
    "Fetcher",
    "HttpStateStore",
    "InMemoryStateStore",
    "Outcome",
    "RateLimiter",
    "RobotsCache",
    "RobotsRules",
    "SourceState",
    "archive_extension",
    "archive_raw",
    "assert_honest_user_agent",
    "host_of",
    "origin_of",
    "request_url",
    "resolve_url",
    "source_key",
    "source_label",
    "split_content_type",
]
