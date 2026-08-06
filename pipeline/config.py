"""Registry and runtime configuration.

Loads and validates ``sources.yaml`` — the single place adapters are named —
plus the process-level settings that come from the environment rather than git.

Responsibilities:

- Parse ``sources.yaml`` into validated objects (Pydantic), applying the
  top-level ``defaults`` block to each space and source.
- Resolve the User-Agent contact from ``$MAKER_CALENDAR_CONTACT`` and **fail
  loudly** if it is unset or still points at ``example.com``. A run with no real
  contact is a bug, not a degraded mode — the whole robots.txt argument in
  ``CLAUDE.md`` depends on someone being able to reach us. Note that launchd
  does not read the shell profile, so ``.env`` must be loaded explicitly.
- Expose the horizon, per-host rate limits, and the paths for ``raw/``, ``db/``,
  ``out/`` and ``logs/`` (all gitignored, all created at runtime).

Every model sets ``extra="forbid"``. An unknown adapter name, filter key or
health key is an **error**, never a warning: a typo that silently does nothing
at 03:15 is the exact failure mode this project keeps trying to eliminate, and
it is indistinguishable from a source going quietly dead.

Implemented by issue 0005 (registry loader with fail-loud contact validation).
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Mapping
from datetime import date
from pathlib import Path
from typing import Any, Literal, NamedTuple, get_args

import yaml
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError, model_validator

# --------------------------------------------------------------------------- paths

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCES_YAML = REPO_ROOT / "sources.yaml"

# Runtime directories. All gitignored; created by the stage that writes to them,
# never here — importing the config must not touch the filesystem.
RAW_DIR = REPO_ROOT / "raw"
DB_DIR = REPO_ROOT / "db"
OUT_DIR = REPO_ROOT / "out"
LOGS_DIR = REPO_ROOT / "logs"

# --------------------------------------------------------------------------- contact

CONTACT_ENV_VAR = "MAKER_CALENDAR_CONTACT"

# The placeholder token used inside sources.yaml, e.g.
#   user_agent: "bayarea-maker-calendar/0.1 (+${MAKER_CALENDAR_CONTACT})"
_PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ConfigError(Exception):
    """Base class for every configuration failure. Always fatal at startup."""


class ContactError(ConfigError):
    """``$MAKER_CALENDAR_CONTACT`` is missing, empty or still a placeholder."""


class RegistryError(ConfigError):
    """``sources.yaml`` is malformed, or names something we do not implement."""


# --------------------------------------------------------------------------- adapters

Adapter = Literal[
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
]

#: Every adapter the registry is permitted to name. Adding one means adding it
#: here, to the header comment in ``sources.yaml``, and to ``pipeline/adapters/``.
KNOWN_ADAPTERS: frozenset[str] = frozenset(get_args(Adapter))

#: Adapters that carry a ``calendar_id`` instead of a ``url``.
CALENDAR_ID_ADAPTERS: frozenset[str] = frozenset({"gcal_ics"})

#: Sentinel used in ``sources.yaml`` for a source whose real URL is not known
#: yet (currently sequoia-fabrica / bookwhen-public — see issue 0002). Loading
#: must not crash on it; fetching must skip it. See ``Source.is_todo``.
TODO_URL = "TODO"


# --------------------------------------------------------------------------- models


class Defaults(BaseModel):
    """The top-level ``defaults`` block. Inherited by every space."""

    model_config = ConfigDict(extra="forbid")

    timezone: str
    user_agent: str
    rate_limit_seconds: float = Field(default=2.0, gt=0)
    horizon_days: int = Field(default=120, gt=0)


class Filters(BaseModel):
    """Post-parse, pre-dedupe event filters for one source.

    ``extra="forbid"`` is load-bearing: filters drop events *silently*, so a
    misspelled key would quietly widen or narrow a space's calendar with no
    error anywhere.
    """

    model_config = ConfigDict(extra="forbid")

    location_contains: list[str] = Field(default_factory=list)
    title_contains: list[str] = Field(default_factory=list)
    location_excludes: list[str] = Field(default_factory=list)
    title_excludes: list[str] = Field(default_factory=list)
    categories_exclude: list[str] = Field(default_factory=list)

    # CLAUDE.md: 15% of Frontier Tower's events set LOCATION to a bare Luma URL.
    # Default to keeping those rather than dropping them at the filter step.
    #
    # The default is True, and issue 0010 corrected it from False: the invariant
    # says an event whose LOCATION is empty or a bare URL is kept unless the
    # registry says otherwise, and a default of False made every source that
    # writes `location_contains` without thinking about it drop those events
    # silently -- which is precisely the failure the invariant names. Registry
    # entries that mean it still write `location_allow_when_missing: false` and
    # get the strict behavior. Keeping a possibly-off-site event is visible in
    # the published calendar and recoverable; dropping a real one is neither.
    location_allow_when_missing: bool = True

    @property
    def is_empty(self) -> bool:
        return self == Filters()


class Health(BaseModel):
    """Per-space or per-source overrides for the publish gates.

    Counting events is necessary but not sufficient; these four keys cover the
    cases a count-based gate gets wrong (see the health-gate table in
    ``CLAUDE.md``).
    """

    model_config = ConfigDict(extra="forbid")

    allow_zero: bool = False
    ignore_count_drop: bool = False
    require_nonzero_once: bool = False
    max_stale_days: int | None = Field(default=None, gt=0)


class SpaceApi(BaseModel):
    """Whether the space publishes a https://spaceapi.io endpoint.

    Not a data source today — tracked because it is the best thing to ask a
    space for.
    """

    model_config = ConfigDict(extra="forbid")

    url: str | None = None
    status: Literal["live", "dead", "none"] = "none"
    checked: date | None = None


class Source(BaseModel):
    """One feed belonging to one space."""

    model_config = ConfigDict(extra="forbid")

    adapter: Adapter
    url: str | None = None
    calendar_id: str | None = None
    script_id: str | None = None

    # Which bespoke document shape a `json` source has. Required for that
    # adapter for the same reason `script_id` is required for `embedded_json`:
    # the two registered documents (a dict keyed "YYYY-MM-DD_<amiliaId>" and a
    # WooCommerce product list) have nothing in common, and sniffing which one
    # arrived would be a guess. Validated against pipeline.adapters.json_doc.SHAPES.
    shape: str | None = None

    # `params` is load-bearing, not cosmetic. The Crucible's Store API returns
    # HTTP 200 and valid JSON with 72% of the catalog missing when `orderby=date`
    # is absent -- see `min_total` below, which is the second, independent guard.
    params: dict[str, Any] = Field(default_factory=dict)

    # The smallest item total this source is registered to carry, checked
    # against the count the response itself reports (`X-WP-Total`). A source
    # reporting fewer is treated as **truncated**, not as a small catalog: the
    # recorded case is `wc/store/v1/products` without `orderby=date`, which
    # answers 200 with valid JSON and 98 products instead of 353. This exists so
    # the amputation is caught from the *response* even when the request looked
    # right.
    min_total: int | None = Field(default=None, gt=0)

    label: str | None = None
    trust: int = Field(default=0, ge=0)

    # Absence is not the same as false: an unverified source is one nobody has
    # actually fetched, and it stays untrusted until someone does.
    verified: bool = False
    enabled: bool = True

    notes: str | None = None
    filters: Filters = Field(default_factory=Filters)
    health: Health = Field(default_factory=Health)

    @model_validator(mode="after")
    def _check_target(self) -> Source:
        if self.adapter in CALENDAR_ID_ADAPTERS:
            if not self.calendar_id:
                raise ValueError(f"adapter {self.adapter!r} requires calendar_id")
            if self.url:
                raise ValueError(
                    f"adapter {self.adapter!r} takes calendar_id, not url ({self.url!r})"
                )
        else:
            if not self.url:
                raise ValueError(f"adapter {self.adapter!r} requires url")
            if self.calendar_id:
                raise ValueError(
                    f"adapter {self.adapter!r} takes url, not calendar_id"
                )
        if self.adapter == "embedded_json" and not self.script_id:
            raise ValueError("adapter 'embedded_json' requires script_id")
        if self.adapter == "json":
            # Imported here rather than at module scope: pipeline.adapters
            # imports pipeline.config, and a top-level import would be a cycle.
            from pipeline.adapters.json_doc import SHAPES

            if not self.shape:
                raise ValueError(
                    "adapter 'json' requires shape. The adapter maps a bespoke "
                    "document onto the event schema and the registered documents "
                    "have nothing in common, so sniffing the shape would be a "
                    f"guess. Known shapes: {', '.join(sorted(SHAPES))}"
                )
            if self.shape not in SHAPES:
                raise ValueError(
                    f"unknown shape {self.shape!r}; known shapes: "
                    f"{', '.join(sorted(SHAPES))}"
                )
        elif self.shape:
            raise ValueError(
                f"adapter {self.adapter!r} takes no shape ({self.shape!r}); "
                "shape is the 'json' adapter's document map"
            )
        return self

    @property
    def is_todo(self) -> bool:
        """True when the URL is the ``TODO`` placeholder rather than a real feed.

        One source is in this state (sequoia-fabrica / bookwhen-public, issue
        0002). The loader accepts it so the registry keeps documenting it; the
        fetch layer must skip it rather than requesting the literal string.
        """
        return bool(self.url) and self.url.strip().upper().startswith(TODO_URL)

    @property
    def target(self) -> str:
        """The feed handle: the URL, or the calendar id for ``gcal_ics``."""
        return self.url or self.calendar_id or ""

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        return f"{self.label or self.adapter} ({self.adapter})"


class Space(BaseModel):
    """One makerspace and the feeds it publishes."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    city: str
    region: str
    url: str

    # Some feeds ship a stale or absent venue; this wins over the feed's own.
    address_override: str | None = None

    # Overrides defaults.rate_limit_seconds. Ace needs 10 because its robots.txt
    # sets Crawl-delay: 10, which is above the global default.
    rate_limit_seconds: float | None = Field(default=None, gt=0)

    notes: str | None = None
    health: Health = Field(default_factory=Health)
    spaceapi: SpaceApi | None = None

    # An empty list is valid and meaningful: Lower 48 has no machine-readable
    # event source at all. That is a documented state, not a broken entry.
    sources: list[Source] = Field(default_factory=list)

    _defaults: Defaults | None = PrivateAttr(default=None)

    @model_validator(mode="after")
    def _check_labels_unique(self) -> Space:
        labels = [src.label for src in self.sources if src.label]
        duplicates = {label for label in labels if labels.count(label) > 1}
        if duplicates:
            raise ValueError(f"duplicate source labels in space {self.id!r}: {sorted(duplicates)}")
        return self

    @property
    def effective_rate_limit_seconds(self) -> float:
        """Per-space override if set, otherwise ``defaults.rate_limit_seconds``."""
        if self.rate_limit_seconds is not None:
            return self.rate_limit_seconds
        if self._defaults is None:
            raise ConfigError(
                f"space {self.id!r} has no defaults attached; build it through "
                "load_registry() rather than constructing Space directly"
            )
        return self._defaults.rate_limit_seconds

    @property
    def enabled_sources(self) -> list[Source]:
        """The run set: sources this run will actually fetch."""
        return [src for src in self.sources if src.enabled]

    @property
    def skipped_sources(self) -> list[Source]:
        """``enabled: false`` sources — loaded, not fetched, reportable as skipped."""
        return [src for src in self.sources if not src.enabled]

    @property
    def todo_sources(self) -> list[Source]:
        """Sources whose URL is still the ``TODO`` placeholder."""
        return [src for src in self.sources if src.is_todo]


class SourceRef(NamedTuple):
    """A source together with the space it belongs to.

    The fetch layer needs both: the space carries the rate limit and the
    address override, the source carries the URL and the adapter.
    """

    space: Space
    source: Source


class Registry(BaseModel):
    """The whole of ``sources.yaml``, validated."""

    model_config = ConfigDict(extra="forbid")

    defaults: Defaults
    spaces: list[Space]

    @model_validator(mode="after")
    def _wire_defaults(self) -> Registry:
        ids = [space.id for space in self.spaces]
        duplicates = {sid for sid in ids if ids.count(sid) > 1}
        if duplicates:
            raise ValueError(f"duplicate space ids: {sorted(duplicates)}")
        for space in self.spaces:
            space._defaults = self.defaults
        return self

    # -- convenience accessors -------------------------------------------------

    @property
    def user_agent(self) -> str:
        """The fully expanded User-Agent. Never contains a placeholder."""
        return self.defaults.user_agent

    @property
    def horizon_days(self) -> int:
        return self.defaults.horizon_days

    @property
    def timezone(self) -> str:
        return self.defaults.timezone

    @property
    def spaces_by_id(self) -> dict[str, Space]:
        return {space.id: space for space in self.spaces}

    def space(self, space_id: str) -> Space:
        try:
            return self.spaces_by_id[space_id]
        except KeyError:
            raise KeyError(f"no space with id {space_id!r}") from None

    @property
    def all_sources(self) -> list[SourceRef]:
        """Every source in the registry, enabled or not."""
        return [SourceRef(space, src) for space in self.spaces for src in space.sources]

    @property
    def enabled_sources(self) -> list[SourceRef]:
        """The run set."""
        return [ref for ref in self.all_sources if ref.source.enabled]

    @property
    def skipped_sources(self) -> list[SourceRef]:
        """``enabled: false`` sources, for the "skipped" line in the run report."""
        return [ref for ref in self.all_sources if not ref.source.enabled]

    @property
    def todo_sources(self) -> list[SourceRef]:
        """Sources still pointing at the ``TODO`` placeholder (issue 0002)."""
        return [ref for ref in self.all_sources if ref.source.is_todo]

    def iter_enabled(self) -> Iterator[SourceRef]:
        """Iterate the run set in registry order — spaces one at a time."""
        yield from self.enabled_sources


# --------------------------------------------------------------------------- loading


def resolve_contact(env: Mapping[str, str] | None = None) -> str:
    """Return ``$MAKER_CALENDAR_CONTACT``, or raise ``ContactError``.

    This is the honest-User-Agent half of the robots.txt position in
    ``CLAUDE.md``: we read these sites under a group we match by *not* naming
    ourselves as anything we are not, and the whole argument rests on a site
    operator being able to reach us. A run with no real contact is therefore a
    bug and not a degraded mode — there is deliberately no fallback value.
    """
    env = os.environ if env is None else env
    raw = env.get(CONTACT_ENV_VAR)

    if raw is None:
        raise ContactError(
            f"${CONTACT_ENV_VAR} is not set. It is interpolated into the "
            "User-Agent sent to every site we fetch. Copy .env.example to .env "
            "and set it to an about page that actually resolves. Note that "
            "launchd does not read your shell profile, so the nightly job must "
            "load .env explicitly or carry the value in the plist's "
            "EnvironmentVariables."
        )

    contact = raw.strip()
    if not contact:
        raise ContactError(
            f"${CONTACT_ENV_VAR} is empty. An empty contact is not a degraded "
            "mode, it is a bug: see the robots.txt section of CLAUDE.md."
        )
    if "example.com" in contact.lower():
        raise ContactError(
            f"${CONTACT_ENV_VAR} is still the {contact!r} placeholder. Point it "
            "at a page you actually publish — a User-Agent pointing at a 404 is "
            "worse than no contact, because it looks like a bot performing "
            "accountability."
        )
    if not contact.startswith(("http://", "https://")):
        raise ContactError(
            f"${CONTACT_ENV_VAR} must be an about-page URL (http:// or https://), "
            f"not {contact!r}. It is a URL rather than an email so no address is "
            "exposed in git, in a header, or in anyone's access logs."
        )
    if any(char.isspace() for char in contact) or ")" in contact:
        raise ContactError(
            f"${CONTACT_ENV_VAR} contains whitespace or ')' ({contact!r}), which "
            "would corrupt the User-Agent comment field."
        )
    return contact


def expand_placeholders(value: Any, values: Mapping[str, str]) -> Any:
    """Recursively expand ``${NAME}`` placeholders in the raw registry data.

    An unrecognized placeholder name raises rather than being left literal: a
    typo would otherwise ship ``${MAKER_CALENDER_CONTACT}`` verbatim in the
    User-Agent of every request.
    """
    if isinstance(value, str):

        def substitute(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in values:
                raise RegistryError(
                    f"unknown placeholder ${{{name}}} in sources.yaml; "
                    f"known placeholders: {', '.join(sorted(values))}"
                )
            return values[name]

        return _PLACEHOLDER_RE.sub(substitute, value)
    if isinstance(value, dict):
        return {key: expand_placeholders(item, values) for key, item in value.items()}
    if isinstance(value, list):
        return [expand_placeholders(item, values) for item in value]
    return value


def load_registry(
    path: str | Path = SOURCES_YAML,
    *,
    env: Mapping[str, str] | None = None,
) -> Registry:
    """Load, expand and validate ``sources.yaml``.

    Raises ``ContactError`` if ``$MAKER_CALENDAR_CONTACT`` is unusable, and
    ``RegistryError`` if the file is malformed or names an adapter, filter key
    or health key we do not implement. Both are ``ConfigError``, and both are
    meant to stop the run before a single request goes out.
    """
    path = Path(path)

    # Contact first: a bad contact is a bug regardless of how good the YAML is,
    # and failing on it before parsing keeps the error message unambiguous.
    contact = resolve_contact(env)

    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RegistryError(f"cannot read registry at {path}: {exc}") from exc

    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise RegistryError(f"{path} is not valid YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise RegistryError(f"{path} must contain a mapping, got {type(data).__name__}")

    data = expand_placeholders(data, {CONTACT_ENV_VAR: contact})

    try:
        return Registry.model_validate(data)
    except ValidationError as exc:
        raise RegistryError(f"{path} failed validation:\n{exc}") from exc


__all__ = [
    "CALENDAR_ID_ADAPTERS",
    "CONTACT_ENV_VAR",
    "DB_DIR",
    "KNOWN_ADAPTERS",
    "LOGS_DIR",
    "OUT_DIR",
    "RAW_DIR",
    "REPO_ROOT",
    "SOURCES_YAML",
    "TODO_URL",
    "Adapter",
    "ConfigError",
    "ContactError",
    "Defaults",
    "Filters",
    "Health",
    "Registry",
    "RegistryError",
    "Source",
    "SourceRef",
    "Space",
    "SpaceApi",
    "expand_placeholders",
    "load_registry",
    "resolve_contact",
]
