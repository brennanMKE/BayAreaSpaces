"""The command line: one deterministic process that iterates the registry.

``python -m pipeline run`` is the whole nightly job. No agent is in the loop and
no per-space context is managed, because a ``for`` loop over ``sources.yaml``
costs nothing::

    launchd 03:15  ->  python -m pipeline run
                        |- for each space, for each enabled source:
                        |     fetch (conditional GET, rate limit, robots) -> raw/
                        |     adapter -> normalize -> filter
                        |- dedupe across all spaces        (issue 0015, not yet)
                        |- enrich  <- the only model stage (issue 0029, not yet)
                        |- health gates                    (issue 0016, not yet)
                        `- publish (out/, later Postgres) + health.json (0017)

Spaces are sequenced one at a time. That is not a concession — it falls out of
the per-host rate limiting in :mod:`pipeline.fetch`, the run is dominated by
deliberate waiting rather than compute, and the whole thing is minutes on this
hardware.

The surface
-----------

::

    python -m pipeline run [--dry-run] [--space <id>] [--no-llm] [--horizon-days N]
    python -m pipeline validate

``validate`` loads the registry and reports what it found without making a
single request. It exists because issue 0001 (the bot about page must resolve
before the first fetch) blocks live fetching, and the config path still needs a
way to be exercised.

Six decisions worth stating
---------------------------

**Unimplemented adapters skip, they do not crash.** Two of the ten adapter names
in ``sources.yaml`` are implemented today (``ics``, ``gcal_ics``); the other
eight are issues 0019-0025 and 0028. A registry entry naming one of those is
reported as skipped with the issue number, so a run today produces the Tier A
calendar rather than a traceback.

**The model is never load-bearing.** If LM Studio is not answering on
``localhost:1234`` the model stages are skipped and Tier A/B is emitted anyway.
Structured data must never be held hostage to the model being up — and 20 of
this registry's 21 verified sources are deterministically parseable, so that is
almost the whole calendar.

**A single-space run never publishes.** ``--space`` is the workhorse for adapter
development and for the repair workflow, and its event set is *by construction*
one space's worth. Writing that to ``out/calendar.ics`` would replace the merged
calendar with a tenth of itself, which is exactly the silent-loss failure this
project keeps designing against. ``--space`` therefore always writes to
``out/.staging/``, like ``--dry-run``.

**An empty event set is never published either.** With no events there is
nothing to write that is not a deletion, and deleting the published calendar
because every source happened to be unimplemented tonight is not a thing this
program will do. Issue 0016 turns that instinct into real gates.

**The exit code is the launchd log.** See :data:`EXIT_OK` and friends: 0 for a
clean run, 2 for a configuration error, and :data:`EXIT_HEALTH_BLOCKED` when the
health gates refuse publication. The gates themselves are issue 0016, so the
seam in :func:`evaluate_health` returns "not blocked" today and the run exits 0;
the branch that returns the non-zero code is already wired so 0016 only has to
fill in the decision.

**A failed source republishes rather than disappearing.** Issue 0014 opens the
SQLite store at run start and hands it to the ``Fetcher``, which is what makes
conditional GET real and what makes carry-forward possible. A ``failed`` source
emits its last-known-good events, horizon-clipped, until the third consecutive
night; a ``blocked`` one emits nothing, ever. ``--dry-run`` reads that store and
writes none of it. The reasoning for all of it lives in
:mod:`pipeline.carry_forward` and in :func:`run_pipeline`.

Implemented by issue 0012, extended by issue 0014.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys
import time
from collections.abc import Callable, Iterator, MutableMapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from pipeline import __version__
from pipeline.adapters.gcal_ics import parse_gcal_ics
from pipeline.adapters.ics import IcsParse, parse_ics
from pipeline.carry_forward import CarryForward, apply_carry_forward
from pipeline.config import (
    OUT_DIR,
    RAW_DIR,
    REPO_ROOT,
    SOURCES_YAML,
    ConfigError,
    Registry,
    Source,
    SourceRef,
    Space,
    load_registry,
)
from pipeline.emit_ics import EmitResult, emit_ics
from pipeline.fetch import FetchError, FetchResult, Fetcher, Outcome, request_url, source_label
from pipeline.filters import filter_normalization
from pipeline.normalize import Event, normalize_ics
from pipeline.store import DEFAULT_DB_PATH, ReadOnlyStore, Store, open_store

LOG = logging.getLogger("pipeline.cli")

# --------------------------------------------------------------------------- exit codes

#: Everything ran and whatever could be published was published.
EXIT_OK = 0

#: **Health gates blocked publication.** Nothing was written; the previously
#: published files are untouched and still correct-ish. launchd's log shows a
#: non-zero exit, which is the entire point of reserving a code for this rather
#: than letting a blocked publish look like a successful run. Issue 0016 owns
#: the decision; :func:`evaluate_health` is the seam it fills in.
EXIT_HEALTH_BLOCKED = 1

#: Configuration is unusable: ``$MAKER_CALENDAR_CONTACT`` missing or still a
#: placeholder, ``sources.yaml`` malformed, ``--space`` naming a space that does
#: not exist. Nothing was fetched. Reported as a message, never a traceback.
EXIT_CONFIG = 2

#: The run itself broke in a way no source-level handler caught. A traceback is
#: logged, because this one *is* a bug in our code.
EXIT_ERROR = 3

# --------------------------------------------------------------------------- knobs

#: ``.env`` next to ``sources.yaml``. launchd does not read the shell profile,
#: so the nightly job loads this explicitly — see :func:`load_dotenv`.
ENV_FILE = REPO_ROOT / ".env"

#: LM Studio's OpenAI-compatible API. Checked, never required.
LM_STUDIO_BASE_URL = "http://localhost:1234/v1"

#: Cheapest endpoint that proves the server is answering *and* has a model.
LM_STUDIO_PROBE_PATH = "/models"

#: The health check is a liveness probe, not a request. If LM Studio needs more
#: than this to say hello, the run is better off skipping the model stages.
LM_STUDIO_TIMEOUT_SECONDS = 2.0

#: Where ``--dry-run`` (and any single-space run) writes instead of ``out/``.
STAGING_DIRNAME = ".staging"


# --------------------------------------------------------------------------- .env


def parse_env_file(text: str) -> dict[str, str]:
    """Parse ``KEY=value`` lines. Deliberately about a dozen lines of code.

    Adding a dependency to read a five-line file would be worse than the file.
    Supported: blank lines, ``#`` comments, an optional ``export`` prefix, and
    surrounding single or double quotes. Not supported, on purpose: variable
    interpolation, multi-line values, and stripping ``#`` from an *unquoted*
    value — a URL may legitimately contain one, and silently truncating the
    contact URL is precisely the class of quiet damage this project avoids.
    """
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        name, sep, value = line.partition("=")
        if not sep:
            continue
        name = name.strip()
        if not name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[name] = value
    return values


def load_dotenv(
    path: Path | str | None = ENV_FILE,
    *,
    env: MutableMapping[str, str] | None = None,
) -> dict[str, str]:
    """Load ``.env`` into *env* **without overriding anything already set**.

    launchd does not read your shell profile, so ``$MAKER_CALENDAR_CONTACT``
    would be absent at 03:15 and the run would (correctly) refuse to start. This
    is the fix, and issue 0031's plist depends on it working.

    The real environment always wins. A committed-adjacent file must never be
    able to silently replace a value an operator set deliberately on the command
    line or in the plist — that would make ``FOO=bar python -m pipeline run``
    lie, and debugging a value that comes from a file you did not look at is the
    worst half hour in configuration.

    Returns only the names it actually applied. A missing file is normal and
    returns ``{}``.
    """
    env = os.environ if env is None else env
    if path is None:
        return {}
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:  # pragma: no cover - unreadable but present
        LOG.warning("could not read %s: %s", path, exc)
        return {}

    applied: dict[str, str] = {}
    for name, value in parse_env_file(text).items():
        if name in env:
            continue
        env[name] = value
        applied[name] = value
    if applied:
        LOG.debug("loaded %s from %s", ", ".join(sorted(applied)), path)
    return applied


# --------------------------------------------------------------------------- adapters

#: What an adapter entry point looks like. ``ics`` and ``gcal_ics`` both take a
#: :class:`~pipeline.fetch.FetchResult` and return an
#: :class:`~pipeline.adapters.ics.IcsParse`; adapters 0019-0028 are expected to
#: return the same shape or an equivalent one with the same counts.
ParseFn = Callable[..., IcsParse]


@dataclass(frozen=True)
class AdapterEntry:
    """One row of the dispatch table: a name, an issue, and maybe a parser."""

    name: str
    #: The issue that implements it. Present for implemented adapters too, so a
    #: skip message and a provenance question have the same answer.
    issue: str
    parse: ParseFn | None = None

    @property
    def implemented(self) -> bool:
        return self.parse is not None

    @property
    def skip_message(self) -> str:
        return (
            f"adapter {self.name!r} is not yet implemented, see issue {self.issue}"
        )


#: The dispatch table. ``sources.yaml`` is the only place adapters are *named*;
#: this is the only place they are *resolved*. Eight of the ten are still
#: pending, and a registry entry naming one is skipped with its issue number
#: rather than crashing the run.
ADAPTERS: dict[str, AdapterEntry] = {
    "ics": AdapterEntry("ics", "0007", parse_ics),
    "gcal_ics": AdapterEntry("gcal_ics", "0008", parse_gcal_ics),
    "tribe_rest": AdapterEntry("tribe_rest", "0019"),
    "jsonld": AdapterEntry("jsonld", "0020"),
    "embedded_json": AdapterEntry("embedded_json", "0021"),
    "json": AdapterEntry("json", "0022"),
    "nextdata": AdapterEntry("nextdata", "0023"),
    "bookwhen_html": AdapterEntry("bookwhen_html", "0024"),
    "rss": AdapterEntry("rss", "0025"),
    "llm_html": AdapterEntry("llm_html", "0028"),
}


def implemented_adapters() -> frozenset[str]:
    """Adapter names this build can actually run."""
    return frozenset(name for name, entry in ADAPTERS.items() if entry.implemented)


def is_runnable(ref: SourceRef) -> bool:
    """True when this source would be fetched and parsed by a run right now."""
    source = ref.source
    entry = ADAPTERS.get(source.adapter)
    return bool(source.enabled and not source.is_todo and entry and entry.implemented)


# --------------------------------------------------------------------------- LM Studio


@dataclass(frozen=True)
class LmStudioStatus:
    """Whether the model stages may run. Never a reason to fail the run."""

    available: bool
    base_url: str = LM_STUDIO_BASE_URL
    #: False when we did not even look (``--no-llm``).
    checked: bool = True
    reason: str = ""
    model_count: int | None = None
    elapsed_seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "checked": self.checked,
            "base_url": self.base_url,
            "reason": self.reason,
            "model_count": self.model_count,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
        }


def probe_lm_studio(
    base_url: str = LM_STUDIO_BASE_URL,
    *,
    timeout: float = LM_STUDIO_TIMEOUT_SECONDS,
    transport: httpx.BaseTransport | None = None,
) -> LmStudioStatus:
    """Ask LM Studio whether it is up. Answers ``False`` rather than raising.

    CLAUDE.md: "If LM Studio is not answering, skip the model stages and publish
    Tier A/B only rather than failing the run." Every failure mode — connection
    refused, timeout, a proxy answering 502, a non-JSON body — is the same
    answer here, because the only decision downstream is whether to call it.
    """
    url = f"{base_url.rstrip('/')}{LM_STUDIO_PROBE_PATH}"
    started = time.perf_counter()
    try:
        with httpx.Client(timeout=timeout, transport=transport) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        return LmStudioStatus(
            available=False,
            base_url=base_url,
            reason=f"{type(exc).__name__}: {exc}",
            elapsed_seconds=time.perf_counter() - started,
        )

    elapsed = time.perf_counter() - started
    if response.status_code != 200:
        return LmStudioStatus(
            available=False,
            base_url=base_url,
            reason=f"HTTP {response.status_code} from {url}",
            elapsed_seconds=elapsed,
        )
    try:
        models = response.json().get("data") or []
    except ValueError:
        models = []
    return LmStudioStatus(
        available=True,
        base_url=base_url,
        reason=f"HTTP 200 from {url}",
        model_count=len(models),
        elapsed_seconds=elapsed,
    )


# --------------------------------------------------------------------------- records


@dataclass
class SourceRecord:
    """The structured per-source record. **Issue 0017 builds ``health.json`` from
    exactly this.**

    Emitted as data rather than as a formatted string on purpose: the six fields
    the issue names — ``status``, ``content_type``, ``bytes``, ``raw_count``,
    ``horizon_count``, ``elapsed_seconds`` — are what the gates in issue 0016
    read, and a log line someone has to re-parse tomorrow is not a record.

    ``raw_count`` and ``horizon_count`` are kept apart because in this registry
    the first says almost nothing: Sequoia Fabrica ships 89 VEVENTs for ~7 live
    events, Maker Nexus 3645 for 171. **Gates count the second one.**
    """

    space_id: str
    label: str
    adapter: str
    url: str = ""
    space_name: str = ""

    #: ``ok`` | ``not_modified`` | ``skipped`` | ``blocked`` | ``failed`` | ``error``
    status: str = "skipped"
    #: For ``skipped``: ``disabled`` | ``todo`` | ``adapter_not_implemented`` |
    #: ``adapter_unknown``. ``None`` otherwise.
    skipped_because: str | None = None
    implemented: bool = False
    #: The issue that implements (or implemented) this adapter.
    issue: str = ""

    # -- transport -------------------------------------------------------------
    http_status: int | None = None
    content_type: str | None = None
    bytes: int = 0
    attempts: int = 0
    conditional: bool = False
    redirected: bool = False
    raw_path: str | None = None
    rate_limit_seconds: float = 0.0
    fetch_seconds: float = 0.0

    # -- counts, each one a different question ---------------------------------
    #: Items the adapter saw before expansion (VEVENTs, for ICS).
    raw_count: int = 0
    #: Post-expansion events inside the horizon. The health-gate number.
    horizon_count: int = 0
    #: Survivors of ``normalize`` (sanity window, quarantine).
    normalized_count: int = 0
    #: Survivors of the registry's ``filters`` block. What reaches the calendar.
    event_count: int = 0
    dropped_count: int = 0
    quarantined_count: int = 0
    filtered_out_count: int = 0
    #: Floating times interpreted by policy. Non-zero is worth a look.
    tz_assumed_count: int = 0

    # -- staleness (issue 0016's ``max_stale_days`` runs on these) -------------
    last_change: str | None = None
    stale_days: float | None = None

    # -- carry-forward (issue 0014) --------------------------------------------
    #: This source failed and republished its last-known-good events. Never set
    #: for ``blocked`` — see :mod:`pipeline.carry_forward`.
    carried_forward: bool = False
    #: HTTP 304: the stored set was reused because the server says it is current.
    #: Not staleness, not a failure, and deliberately a different field.
    reused_unchanged: bool = False
    carry_forward_count: int = 0
    #: Stored events that have since fallen out of the horizon. The calendar
    #: decays rather than freezing.
    carry_forward_dropped_count: int = 0
    #: **The age of the data**, which is the number issue 0017 surfaces.
    carry_forward_age_seconds: float | None = None
    last_known_good_at: str | None = None
    carry_forward_note: str = ""
    #: Consecutive failed nights including tonight. Three escalates.
    consecutive_failures: int = 0
    escalated: bool = False
    #: Set only when escalated. Issue 0016's gates and issue 0027's alerting.
    alert: str | None = None

    # -- verdicts --------------------------------------------------------------
    problem: str | None = None
    reason: str | None = None
    error: str | None = None
    elapsed_seconds: float = 0.0

    @property
    def ran(self) -> bool:
        """True when the source was actually fetched and parsed."""
        return self.status in ("ok", "not_modified")

    @property
    def carry_forward_age_days(self) -> float | None:
        """How old the republished data is, in days. ``None`` when fresh."""
        if self.carry_forward_age_seconds is None:
            return None
        return self.carry_forward_age_seconds / 86400.0

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready. The wire form issue 0017 writes into ``health.json``."""
        return {
            "space_id": self.space_id,
            "space_name": self.space_name,
            "label": self.label,
            "adapter": self.adapter,
            "url": self.url,
            "status": self.status,
            "skipped_because": self.skipped_because,
            "implemented": self.implemented,
            "issue": self.issue,
            "http_status": self.http_status,
            "content_type": self.content_type,
            "bytes": self.bytes,
            "attempts": self.attempts,
            "conditional": self.conditional,
            "redirected": self.redirected,
            "raw_path": self.raw_path,
            "rate_limit_seconds": round(self.rate_limit_seconds, 3),
            "fetch_seconds": round(self.fetch_seconds, 3),
            "raw_count": self.raw_count,
            "horizon_count": self.horizon_count,
            "normalized_count": self.normalized_count,
            "event_count": self.event_count,
            "dropped_count": self.dropped_count,
            "quarantined_count": self.quarantined_count,
            "filtered_out_count": self.filtered_out_count,
            "tz_assumed_count": self.tz_assumed_count,
            "last_change": self.last_change,
            "stale_days": round(self.stale_days, 2) if self.stale_days is not None else None,
            "carried_forward": self.carried_forward,
            "reused_unchanged": self.reused_unchanged,
            "carry_forward_count": self.carry_forward_count,
            "carry_forward_dropped_count": self.carry_forward_dropped_count,
            "carry_forward_age_seconds": (
                round(self.carry_forward_age_seconds, 3)
                if self.carry_forward_age_seconds is not None
                else None
            ),
            "carry_forward_age_days": (
                round(self.carry_forward_age_days, 3)
                if self.carry_forward_age_days is not None
                else None
            ),
            "carry_forward_note": self.carry_forward_note,
            "last_known_good_at": self.last_known_good_at,
            "consecutive_failures": self.consecutive_failures,
            "escalated": self.escalated,
            "alert": self.alert,
            "problem": self.problem,
            "reason": self.reason,
            "error": self.error,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
        }

    @property
    def key(self) -> str:
        return f"{self.space_id}:{self.label}"

    @property
    def carry_forward_line(self) -> str:
        """The carry-forward suffix for :meth:`line`, or ``""``."""
        if self.escalated:
            return (
                f" [ESCALATED: {self.consecutive_failures} consecutive failures, "
                "carry-forward stopped]"
            )
        if self.carried_forward:
            age = (
                f"{self.carry_forward_age_days:.1f}d old"
                if self.carry_forward_age_days is not None
                else "age unknown"
            )
            return (
                f" [carried forward {self.carry_forward_count} events, {age}"
                + (
                    f", {self.carry_forward_dropped_count} out of horizon"
                    if self.carry_forward_dropped_count
                    else ""
                )
                + "]"
            )
        if self.reused_unchanged:
            return f" [reused {self.carry_forward_count} unchanged events]"
        return ""

    def line(self) -> str:
        """One human-readable line for the run report."""
        head = f"{self.key:<44} {self.adapter:<14} {self.status:<13}"
        if self.status in ("skipped", "blocked", "failed", "error"):
            tail = f"{self.reason or self.error or ''}{self.carry_forward_line}"
            return f"{head} {tail}".rstrip()
        detail = (
            f"HTTP {self.http_status} {self.content_type or '-'} "
            f"{self.bytes}B raw={self.raw_count} horizon={self.horizon_count} "
            f"events={self.event_count} in {self.elapsed_seconds:.2f}s"
        )
        return f"{head} {detail}{self.carry_forward_line}"

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        return self.line()


@dataclass(frozen=True)
class HealthDecision:
    """Whether the run may publish. **Issue 0016 fills this in.**

    The seam exists now so the exit-code contract is real code rather than a
    comment: :data:`EXIT_HEALTH_BLOCKED` is returned when
    :attr:`blocks_publication` is true. Today nothing sets it, so a run that got
    this far exits 0.
    """

    blocked: bool = False
    reasons: tuple[str, ...] = ()
    #: The issue that will make this decision mean something.
    gate_issue: str = "0016"
    implemented: bool = False

    @property
    def blocks_publication(self) -> bool:
        return self.blocked

    def as_dict(self) -> dict[str, Any]:
        return {
            "blocked": self.blocked,
            "reasons": list(self.reasons),
            "gate_issue": self.gate_issue,
            "implemented": self.implemented,
        }


def evaluate_health(records: Sequence[SourceRecord]) -> HealthDecision:
    """The health-gate seam. Returns "not blocked" until issue 0016 lands.

    Deliberately not a stub that raises and not a lie that claims to have
    checked: it reports ``implemented=False`` so ``health.json`` (issue 0017)
    can say out loud that no gate ran tonight. Count-based gates alone are not
    sufficient anyway — see the four cases in CLAUDE.md — and half a gate is
    worse than a documented absence.
    """
    return HealthDecision(blocked=False, reasons=(), implemented=False)


@dataclass
class RunReport:
    """Everything one run did. The input to ``health.json`` (issue 0017)."""

    started_at: dt.datetime
    finished_at: dt.datetime | None = None
    dry_run: bool = False
    no_llm: bool = False
    space_filter: str | None = None
    horizon_days: int = 0
    out_dir: Path | None = None
    published: bool = False
    publish_skipped_reason: str | None = None

    #: The store's run row for this run. ``None`` on a ``--dry-run``, which
    #: writes nothing — see :func:`run_pipeline`.
    run_id: int | None = None
    db_path: str | None = None

    records: list[SourceRecord] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    #: One entry per source that republished stored events, failure or 304.
    carry_forwards: list[CarryForward] = field(default_factory=list)
    #: UIDs seen more than once and collapsed to first-seen. Real dedupe is
    #: issue 0015; a non-empty list here is material for it.
    uid_collisions: list[str] = field(default_factory=list)

    lm_studio: LmStudioStatus | None = None
    enrich_skipped_reason: str | None = None
    dedupe_skipped_reason: str | None = None
    health: HealthDecision = field(default_factory=HealthDecision)
    emit: EmitResult | None = None
    exit_code: int = EXIT_OK

    # -- counts ----------------------------------------------------------------

    @property
    def elapsed_seconds(self) -> float:
        if self.finished_at is None:
            return 0.0
        return (self.finished_at - self.started_at).total_seconds()

    @property
    def event_count(self) -> int:
        return len(self.events)

    @property
    def spaces_seen(self) -> int:
        return len({record.space_id for record in self.records})

    @property
    def ran(self) -> list[SourceRecord]:
        return [record for record in self.records if record.ran]

    @property
    def skipped(self) -> list[SourceRecord]:
        return [record for record in self.records if record.status == "skipped"]

    @property
    def failed(self) -> list[SourceRecord]:
        return [
            record
            for record in self.records
            if record.status in ("failed", "blocked", "error")
        ]

    @property
    def carried_forward(self) -> list[SourceRecord]:
        """Sources publishing aged data tonight. Issue 0017's staleness list."""
        return [record for record in self.records if record.carried_forward]

    @property
    def escalated(self) -> list[SourceRecord]:
        """Sources past the third failed night. These want a human."""
        return [record for record in self.records if record.escalated]

    @property
    def carried_forward_event_count(self) -> int:
        return sum(record.carry_forward_count for record in self.carried_forward)

    @property
    def alerts(self) -> list[str]:
        return [record.alert for record in self.records if record.alert]

    def record_for(self, space_id: str, label: str) -> SourceRecord | None:
        for record in self.records:
            if record.space_id == space_id and record.label == label:
                return record
        return None

    def as_dict(self) -> dict[str, Any]:
        """The whole run as JSON-ready data. Issue 0017 writes this out."""
        return {
            "version": __version__,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "dry_run": self.dry_run,
            "no_llm": self.no_llm,
            "space_filter": self.space_filter,
            "horizon_days": self.horizon_days,
            "out_dir": str(self.out_dir) if self.out_dir else None,
            "published": self.published,
            "publish_skipped_reason": self.publish_skipped_reason,
            "run_id": self.run_id,
            "db_path": self.db_path,
            "counts": {
                "spaces": self.spaces_seen,
                "sources": len(self.records),
                "ran": len(self.ran),
                "skipped": len(self.skipped),
                "failed": len(self.failed),
                "events": self.event_count,
                "carried_forward_sources": len(self.carried_forward),
                "carried_forward_events": self.carried_forward_event_count,
                "escalated_sources": len(self.escalated),
            },
            "carry_forward": [plan.as_dict() for plan in self.carry_forwards],
            "alerts": self.alerts,
            "lm_studio": self.lm_studio.as_dict() if self.lm_studio else None,
            "enrich_skipped_reason": self.enrich_skipped_reason,
            "dedupe_skipped_reason": self.dedupe_skipped_reason,
            "uid_collisions": list(self.uid_collisions),
            "health": self.health.as_dict(),
            "emit": self.emit.summary() if self.emit else None,
            "sources": [record.as_dict() for record in self.records],
            "exit_code": self.exit_code,
        }


# --------------------------------------------------------------------------- the run


def _source_url(source: Source) -> str:
    """The URL this source would be fetched from, for reporting.

    Never raises: a malformed ``calendar_id`` is a real error, but it belongs in
    the record for that source and not in the middle of building a log line for
    a source we already decided to skip.
    """
    try:
        return str(request_url(source))
    except Exception:  # pragma: no cover - defensive
        return source.target


def _new_record(space: Space, source: Source) -> SourceRecord:
    entry = ADAPTERS.get(source.adapter)
    return SourceRecord(
        space_id=space.id,
        space_name=space.name,
        label=source_label(source),
        adapter=source.adapter,
        url=_source_url(source),
        implemented=bool(entry and entry.implemented),
        issue=entry.issue if entry else "",
    )


def _log_record(record: SourceRecord) -> None:
    """Emit the record, as data.

    The dict rides along on the log record under ``source_record`` so a JSON
    handler (or issue 0017) can take it verbatim, while a human tailing
    ``logs/`` still sees a readable line.
    """
    level = logging.WARNING if record.status in ("failed", "blocked", "error") else logging.INFO
    LOG.log(level, "%s", record.line(), extra={"source_record": record.as_dict()})


def process_source(
    ref: SourceRef,
    fetcher: Fetcher,
    *,
    horizon_days: int,
    now: dt.datetime,
) -> tuple[SourceRecord, list[Event]]:
    """Fetch, parse, normalize and filter one source. Never raises for a source.

    A single bad source must not be able to take the other twenty-nine with it,
    so anything unexpected lands in the record as ``status="error"``. What is
    *not* swallowed is a configuration error — those are raised before the first
    source is touched.
    """
    space, source = ref
    record = _new_record(space, source)
    started = time.perf_counter()

    entry = ADAPTERS.get(source.adapter)
    if entry is None:
        record.status = "skipped"
        record.skipped_because = "adapter_unknown"
        record.reason = (
            f"adapter {source.adapter!r} is not in the dispatch table; "
            f"known adapters: {', '.join(sorted(ADAPTERS))}"
        )
        return record, []

    if not entry.implemented:
        record.status = "skipped"
        record.skipped_because = "adapter_not_implemented"
        record.reason = entry.skip_message
        return record, []

    try:
        result: FetchResult = fetcher.fetch(ref)
    except FetchError as exc:
        record.status = "error"
        record.error = f"{type(exc).__name__}: {exc}"
        record.elapsed_seconds = time.perf_counter() - started
        return record, []

    record.http_status = result.status_code
    record.content_type = result.content_type
    record.bytes = result.byte_count
    record.attempts = result.attempts
    record.conditional = result.conditional
    record.redirected = result.redirected
    record.raw_path = str(result.raw_path) if result.raw_path else None
    record.rate_limit_seconds = result.rate_limit_delay_seconds
    record.fetch_seconds = result.elapsed_seconds
    record.url = result.final_url or result.url or record.url
    record.reason = result.reason
    record.error = result.error

    if result.outcome is Outcome.BLOCKED:
        record.status = "blocked"
        record.elapsed_seconds = time.perf_counter() - started
        return record, []
    if result.outcome is Outcome.SKIPPED:
        record.status = "skipped"
        record.skipped_because = "todo" if source.is_todo else "disabled"
        record.elapsed_seconds = time.perf_counter() - started
        return record, []
    if result.outcome is Outcome.FAILED:
        record.status = "failed"
        record.elapsed_seconds = time.perf_counter() - started
        return record, []

    try:
        parse = entry.parse(result, horizon_days=horizon_days, now=now)  # type: ignore[misc]
        record.raw_count = parse.vevent_count
        record.horizon_count = parse.event_count
        record.problem = parse.problem.value
        record.last_change = parse.last_change.isoformat() if parse.last_change else None
        record.stale_days = parse.stale_days

        if not parse.ok:
            record.status = "not_modified" if parse.problem.value == "not_modified" else "failed"
            record.error = parse.error
            record.elapsed_seconds = time.perf_counter() - started
            return record, []

        normalization = normalize_ics(
            parse, space=space, source_label=record.label, now=now
        )
        record.normalized_count = normalization.event_count
        record.dropped_count = len(normalization.dropped)
        record.quarantined_count = len(normalization.quarantined)
        record.tz_assumed_count = len(normalization.conversions)

        filtered = filter_normalization(normalization, source.filters)
        record.filtered_out_count = filtered.dropped_count
        record.event_count = filtered.kept_count
        record.status = "ok"
        record.elapsed_seconds = time.perf_counter() - started
        return record, list(filtered.events)
    except Exception as exc:
        # A parse or normalize bug on one feed — including a NaiveDatetimeError,
        # which is a hard invariant violation but a violation *by one source*.
        # Reported, not fatal: the other
        # spaces still have a calendar to publish and this one carries forward.
        record.status = "error"
        record.error = f"{type(exc).__name__}: {exc}"
        record.elapsed_seconds = time.perf_counter() - started
        LOG.exception("%s: unhandled failure while processing", record.key)
        return record, []


def collapse_uid_collisions(events: Sequence[Event]) -> tuple[list[Event], list[str]]:
    """Keep the first event per UID. Returns ``(events, collided_uids)``.

    **Not dedupe** — issue 0015 owns that, and it is a different job: fuzzy title
    plus start-time matching, resolved by the ``trust`` value in
    ``sources.yaml``. This is the floor beneath it.

    :func:`~pipeline.emit_ics.emit_ics` refuses to publish a calendar containing
    two VEVENTs with one UID, and it is right to: a client treats one UID as one
    event, so the duplicate silently loses an occurrence. But that refusal
    aborts the *entire* run — one space syndicating one event through two of its
    own feeds would take the other ten spaces off the calendar with it. Trading
    a whole night's publication for a collision we already know how to survive
    is the wrong side of that bargain.

    First-in-registry-order wins, which is the same precedence dedupe will apply
    once trust ordering exists, and every collision is logged by UID so it is
    visible rather than absorbed.
    """
    seen: dict[str, Event] = {}
    collisions: list[str] = []
    for event in events:
        if event.uid in seen:
            collisions.append(event.uid)
            continue
        seen[event.uid] = event
    return list(seen.values()), collisions


def iter_sources(registry: Registry, space_id: str | None = None) -> Iterator[SourceRef]:
    """Every source, spaces one at a time, in registry order.

    Disabled and ``TODO`` sources are included — they are reported as skipped
    rather than being invisible, because "the registry has 30 sources and the
    run mentioned 26" is a question that should never need asking.
    """
    spaces = registry.spaces if space_id is None else [registry.space(space_id)]
    for space in spaces:
        for source in space.sources:
            yield SourceRef(space, source)


def run_pipeline(
    registry: Registry,
    *,
    space_id: str | None = None,
    dry_run: bool = False,
    no_llm: bool = False,
    horizon_days: int | None = None,
    out_dir: Path | str | None = None,
    raw_dir: Path | str = RAW_DIR,
    db_path: Path | str | None = None,
    store: Store | None = None,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    now: dt.datetime | None = None,
    llm_probe: Callable[[], LmStudioStatus] | None = None,
) -> RunReport:
    """The nightly run, as a function. :func:`main` is the thin shell around it.

    Returns a :class:`RunReport` rather than exiting, so the repair workflow and
    the tests can drive a run and read the result.

    The store (issue 0014)
    ----------------------

    ``store`` or ``db_path`` names the SQLite working store. Passing **neither**
    gives an in-memory one, which is the pre-0014 behavior — a run that forgets
    every ETag and every event when it exits. That is a deliberate default for a
    *library* call: the nightly job goes through :func:`main`, which passes
    :data:`~pipeline.store.DEFAULT_DB_PATH`, and a test or an experiment that
    forgot to name a database should not silently write into the real one.

    The store does three things here, and they happen in this order:

    1. It backs conditional GET (``Fetcher(state=store)``). Sudo Room's feed is
       8 MB and Maker Nexus's is 11 MB; without this the run re-downloads 19 MB
       every night to compute a byte-identical answer.
    2. It answers carry-forward. A failed source republishes what it last
       produced; a 304 replays the same rows because there is no body to parse.
    3. It records the run — one ``record_events`` call per source per run, which
       is the contract :meth:`~pipeline.store.Store.carry_forward_events`
       depends on, since it keys on ``MAX(last_seen)`` and a second call in the
       same run would split one night's set across two timestamps.

    ``--dry-run`` reads all of it and writes none of it
    ---------------------------------------------------

    A dry run fetches through :class:`~pipeline.store.ReadOnlyStore`: stored
    ETags are *used*, so the run is realistic and cheap, and every write is
    dropped. No HTTP state, no events, no run row.

    The other policy — record the dry run's HTTP state, since a validator is
    just a cache — is tempting and wrong. Storing an ETag makes the **next real
    run** receive a 304 for a body this process parsed and then threw away, so
    the published calendar would be assembled from replayed history because a
    debugging invocation spent the only unconditional fetch. And ``--dry-run``
    is precisely the tool you reach for when a source is misbehaving; a
    diagnostic that mutates the state under diagnosis is a trap. The same
    argument covers ``consecutive_failures``: a dry run must never be able to
    push a source toward its third strike and fire an alert.

    A single-space run (``--space``) *does* write. It performs real fetches for
    the sources it touches, and the sources it does not touch simply have no row
    — an absent run is not a broken streak. See
    :meth:`~pipeline.store.Store.consecutive_failed_runs`.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    horizon = horizon_days or registry.horizon_days
    base_out = Path(out_dir) if out_dir is not None else OUT_DIR

    # A single-space run has, by construction, a single space's events. Writing
    # that to the merged calendar would replace it with a tenth of itself.
    staged = dry_run or space_id is not None
    target = base_out / STAGING_DIRNAME if staged else base_out

    report = RunReport(
        started_at=now,
        dry_run=dry_run,
        no_llm=no_llm,
        space_filter=space_id,
        horizon_days=horizon,
        out_dir=target,
    )

    if space_id is not None and not dry_run:
        LOG.info(
            "--space %s: writing to %s. A single-space run never replaces the "
            "merged calendar; use a full run to publish.",
            space_id,
            target,
        )

    # --- the working store ----------------------------------------------------
    owned_store = None
    if store is None:
        owned_store = open_store(db_path) if db_path is not None else Store.in_memory()
        store = owned_store
    report.db_path = None if store.path == ":memory:" else str(store.path)

    # A dry run reads the store and writes nothing. See the docstring; the
    # short version is that spending the next real run's unconditional fetch on
    # a debugging invocation is not a trade this program makes.
    state_store: Any = ReadOnlyStore(store) if dry_run else store
    if not dry_run:
        report.run_id = store.start_run(
            now,
            dry_run=False,
            space_filter=space_id,
            horizon_days=horizon,
            version=__version__,
        )

    # --- fetch -> adapter -> normalize -> filter, one space at a time ---------
    fetcher = Fetcher(
        registry,
        transport=transport,
        raw_dir=raw_dir,
        state=state_store,
        sleep=sleep,
        clock=clock,
    )
    try:
        for ref in iter_sources(registry, space_id):
            record, events = process_source(
                ref, fetcher, horizon_days=horizon, now=now
            )
            report.records.append(record)

            if events:
                # Once per source per run. carry_forward_events() selects the
                # rows sharing MAX(last_seen), so a second call for the same
                # source tonight would split one night's set in two and
                # tomorrow's carry-forward would republish only the tail of it.
                if dry_run:
                    report.events.extend(events)
                else:
                    merge = state_store.record_events(
                        events, run_id=report.run_id, now=now
                    )
                    # The merged copies carry the *stored* first_seen, which is
                    # what RSS pubDate reads (issue 0018). A title edit must not
                    # re-date an event and re-notify every subscriber.
                    report.events.extend(merge.events)
            else:
                plan = apply_carry_forward(
                    record, state_store, now=now, horizon_days=horizon
                )
                if plan is not None:
                    report.carry_forwards.append(plan)
                    report.events.extend(plan.events)
                    if plan.escalated:
                        LOG.error("%s", plan.alert)
                    elif plan.available:
                        LOG.warning("%s", plan.line())
                    if plan.reused_unchanged and not dry_run:
                        # A 304 is the server saying "these bytes are current",
                        # so the set is re-stamped as seen tonight. Carry-forward
                        # after a *failure* deliberately is not: its whole job is
                        # to let the age grow until somebody looks.
                        state_store.record_events(
                            plan.events, run_id=report.run_id, now=now
                        )

            if not dry_run:
                state_store.record_source_run(report.run_id or 0, record)
            _log_record(record)
    finally:
        fetcher.close()

    # --- dedupe (issue 0015) --------------------------------------------------
    #
    # Real dedupe is fuzzy-title-plus-start matching resolved by ``trust``, and
    # it is issue 0015. All that happens tonight is the floor: exact UID
    # collisions are collapsed so one duplicated event cannot abort the publish
    # for every space at once. See collapse_uid_collisions().
    report.dedupe_skipped_reason = "dedupe is not implemented yet (issue 0015)"
    report.events, collisions = collapse_uid_collisions(report.events)
    report.uid_collisions = collisions
    if collisions:
        LOG.warning(
            "%d duplicate UIDs collapsed to first-seen (%s%s). Real dedupe is "
            "issue 0015; this is only the floor that keeps one collision from "
            "aborting the whole publish.",
            len(collisions),
            ", ".join(sorted(set(collisions))[:5]),
            "…" if len(set(collisions)) > 5 else "",
        )
    LOG.info(
        "%s; %d events pass through unmerged",
        report.dedupe_skipped_reason,
        report.event_count,
    )

    # --- enrich: the only model stage, and never load-bearing -----------------
    if no_llm:
        report.lm_studio = LmStudioStatus(
            available=False, checked=False, reason="--no-llm"
        )
        report.enrich_skipped_reason = "--no-llm was passed; the model stages are off"
    else:
        report.lm_studio = (llm_probe or probe_lm_studio)()
        if not report.lm_studio.available:
            report.enrich_skipped_reason = (
                f"LM Studio is not answering at {report.lm_studio.base_url} "
                f"({report.lm_studio.reason}); emitting Tier A/B only"
            )
        else:
            report.enrich_skipped_reason = "enrich is not implemented yet (issue 0029)"
    LOG.info("enrich skipped: %s", report.enrich_skipped_reason)

    # --- health gates (issue 0016) -------------------------------------------
    report.health = evaluate_health(report.records)
    if report.health.blocks_publication:
        # The contract, in code: a blocked publish exits non-zero so launchd's
        # log shows it. Nothing is written; yesterday's files stand.
        report.exit_code = EXIT_HEALTH_BLOCKED
        report.publish_skipped_reason = "health gates blocked publication: " + "; ".join(
            report.health.reasons
        )
        LOG.error("%s", report.publish_skipped_reason)
        report.finished_at = dt.datetime.now(dt.timezone.utc)
        _finish_run(report, store, owned_store)
        return report

    # --- publish --------------------------------------------------------------
    if not report.events:
        # Not a gate — a refusal to write a deletion. An empty calendar is what
        # a subscriber sees when every source broke at once, and overwriting a
        # good file with one is not recoverable from our side.
        report.publish_skipped_reason = (
            "no events survived the pipeline; refusing to overwrite the published "
            "calendar with an empty one"
        )
        LOG.warning("%s", report.publish_skipped_reason)
    else:
        report.emit = emit_ics(
            report.events, spaces=registry, out_dir=target, dtstamp=now
        )
        report.published = True

    report.finished_at = dt.datetime.now(dt.timezone.utc)
    _finish_run(report, store, owned_store)
    return report


def _finish_run(report: RunReport, store: Store, owned: Store | None) -> None:
    """Close the run row and, if we opened the store, close the store.

    A dry run has no row to close. A store handed in by a caller stays open —
    the caller is holding it and will want to read what the run did.
    """
    if report.run_id is not None:
        store.finish_run(
            report.run_id,
            finished_at=report.finished_at,
            event_count=report.event_count,
            published=report.published,
            health_blocked=report.health.blocked,
            health_reasons=report.health.reasons,
            exit_code=report.exit_code,
        )
    if owned is not None:
        owned.close()


# --------------------------------------------------------------------------- output


def print_run_report(report: RunReport, stream: Any = None) -> None:
    """The human-facing summary. ``health.json`` (issue 0017) is the machine one."""
    out = stream if stream is not None else sys.stdout
    print(f"run started {report.started_at.isoformat()}", file=out)
    print(
        f"horizon {report.horizon_days} days"
        + (f"  space={report.space_filter}" if report.space_filter else "")
        + ("  DRY RUN" if report.dry_run else ""),
        file=out,
    )
    print("", file=out)
    for record in report.records:
        print(f"  {record.line()}", file=out)
    print("", file=out)
    print(
        f"sources: {len(report.records)} "
        f"({len(report.ran)} ran, {len(report.skipped)} skipped, "
        f"{len(report.failed)} failed)",
        file=out,
    )
    print(f"events:  {report.event_count}", file=out)
    if report.carried_forward:
        print(
            f"carried: {report.carried_forward_event_count} events from "
            f"{len(report.carried_forward)} failed sources "
            "(republished, not re-fetched)",
            file=out,
        )
    for alert in report.alerts:
        print(f"ALERT:   {alert}", file=out)
    if report.uid_collisions:
        print(
            f"dedupe:  {len(report.uid_collisions)} duplicate UIDs collapsed to "
            "first-seen (real dedupe is issue 0015)",
            file=out,
        )
    if report.enrich_skipped_reason:
        print(f"enrich:  skipped — {report.enrich_skipped_reason}", file=out)
    if not report.health.implemented:
        print(
            f"health:  no gates ran (issue {report.health.gate_issue} implements them)",
            file=out,
        )
    if report.emit is not None:
        print(
            f"emitted: {report.emit.event_count} events across "
            f"{report.emit.space_count} spaces -> {report.emit.merged_path}",
            file=out,
        )
    elif report.publish_skipped_reason:
        print(f"emitted: nothing — {report.publish_skipped_reason}", file=out)
    print(f"elapsed: {report.elapsed_seconds:.1f}s", file=out)


def print_validation(registry: Registry, path: Path, stream: Any = None) -> None:
    """What ``validate`` reports: the registry, with no network anywhere near it."""
    out = stream if stream is not None else sys.stdout
    all_sources = registry.all_sources
    enabled = registry.enabled_sources
    disabled = registry.skipped_sources
    todo = registry.todo_sources
    runnable = [ref for ref in all_sources if is_runnable(ref)]

    print(f"registry:   {path}", file=out)
    print(f"user agent: {registry.user_agent}", file=out)
    print(f"timezone:   {registry.timezone}   horizon: {registry.horizon_days} days", file=out)
    print("", file=out)
    print(f"spaces:  {len(registry.spaces)}", file=out)
    print(
        f"sources: {len(all_sources)} "
        f"({len(enabled)} enabled, {len(disabled)} disabled, {len(todo)} TODO)",
        file=out,
    )
    print(
        f"runnable end to end right now: {len(runnable)} "
        f"(adapters implemented: {', '.join(sorted(implemented_adapters()))})",
        file=out,
    )
    print("", file=out)

    print("adapters:", file=out)
    counts: dict[str, int] = {}
    enabled_counts: dict[str, int] = {}
    for ref in all_sources:
        counts[ref.source.adapter] = counts.get(ref.source.adapter, 0) + 1
    for ref in enabled:
        enabled_counts[ref.source.adapter] = enabled_counts.get(ref.source.adapter, 0) + 1
    for name in sorted(counts, key=lambda key: (-counts[key], key)):
        entry = ADAPTERS.get(name)
        state = (
            f"implemented (issue {entry.issue})"
            if entry and entry.implemented
            else f"NOT implemented (issue {entry.issue})"
            if entry
            else "unknown adapter"
        )
        plural = "source " if counts[name] == 1 else "sources"
        print(
            f"  {name:<16} {counts[name]:>2} {plural} "
            f"({enabled_counts.get(name, 0)} enabled)  {state}",
            file=out,
        )
    print("", file=out)

    print("spaces:", file=out)
    for space in registry.spaces:
        space_runnable = sum(
            1 for source in space.sources if is_runnable(SourceRef(space, source))
        )
        print(
            f"  {space.id:<22} {len(space.sources):>2} sources "
            f"({len(space.enabled_sources)} enabled, {space_runnable} runnable)  "
            f"{space.name}",
            file=out,
        )
        for source in space.sources:
            entry = ADAPTERS.get(source.adapter)
            flags: list[str] = []
            if not source.enabled:
                flags.append("disabled")
            if source.is_todo:
                flags.append("TODO (issue 0002)")
            if entry is None:
                flags.append("unknown adapter")
            elif not entry.implemented:
                flags.append(f"adapter pending (issue {entry.issue})")
            if not source.verified:
                flags.append("unverified")
            suffix = f"  [{', '.join(flags)}]" if flags else ""
            print(
                f"      {source_label(source):<28} {source.adapter}{suffix}",
                file=out,
            )


# --------------------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline",
        description=(
            "Bay Area makerspace event pipeline. 'run' is the nightly job; "
            "'validate' exercises the registry without touching the network."
        ),
    )
    parser.add_argument("--version", action="version", version=f"pipeline {__version__}")
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="log at DEBUG instead of INFO",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="fetch every source and publish the calendar")
    run.add_argument(
        "--dry-run",
        action="store_true",
        help=f"write to out/{STAGING_DIRNAME}/ instead of out/, publishing nothing",
    )
    run.add_argument(
        "--space",
        metavar="ID",
        help=(
            "run one space (the adapter-development and repair workhorse). "
            f"Always writes to out/{STAGING_DIRNAME}/."
        ),
    )
    run.add_argument(
        "--no-llm",
        action="store_true",
        help="skip the enrich stage entirely, without probing LM Studio",
    )
    run.add_argument(
        "--horizon-days",
        type=int,
        metavar="N",
        help="override defaults.horizon_days from sources.yaml",
    )

    sub.add_parser(
        "validate",
        help="load the registry and report what it found; no network",
    )
    return parser


def _configure_logging(verbose: bool = False) -> None:
    """Log to stderr so stdout stays the report. launchd captures both."""
    if logging.getLogger().handlers:
        return
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
        stream=sys.stderr,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    env: MutableMapping[str, str] | None = None,
    env_file: Path | str | None = ENV_FILE,
    registry_path: Path | str = SOURCES_YAML,
    out_dir: Path | str | None = None,
    raw_dir: Path | str = RAW_DIR,
    db_path: Path | str | None = None,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    now: dt.datetime | None = None,
    llm_probe: Callable[[], LmStudioStatus] | None = None,
    stream: Any = None,
) -> int:
    """Parse arguments, load configuration, dispatch. Returns the exit code.

    The keyword arguments after ``argv`` are not CLI flags: they are the seams
    the tests and the repair workflow drive the run through — a mock transport,
    a fake clock, a temporary ``out/``, a throwaway ``db_path``. Nothing here
    reaches the network unless a real transport is used, and nothing writes to
    the real ``db/events.sqlite`` unless ``db_path`` is left alone.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)

    # .env first, and always before the registry: the contact is read at load
    # time and launchd will not have supplied it from a shell profile.
    load_dotenv(env_file, env=env)

    try:
        registry = load_registry(registry_path, env=env)
    except ConfigError as exc:
        # A message, not a traceback. This is the most common startup failure
        # and it is always something the operator can fix in one line.
        print(f"configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    if args.command == "validate":
        print_validation(registry, Path(registry_path), stream=stream)
        return EXIT_OK

    space_id = args.space
    if space_id is not None and space_id not in registry.spaces_by_id:
        print(
            f"configuration error: no space with id {space_id!r}. Known ids: "
            f"{', '.join(sorted(registry.spaces_by_id))}",
            file=sys.stderr,
        )
        return EXIT_CONFIG

    try:
        report = run_pipeline(
            registry,
            space_id=space_id,
            dry_run=args.dry_run,
            no_llm=args.no_llm,
            horizon_days=args.horizon_days,
            out_dir=out_dir,
            raw_dir=raw_dir,
            db_path=DEFAULT_DB_PATH if db_path is None else db_path,
            transport=transport,
            sleep=sleep,
            clock=clock,
            now=now,
            llm_probe=llm_probe,
        )
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except Exception:
        LOG.exception("the run failed")
        return EXIT_ERROR

    print_run_report(report, stream=stream)
    return report.exit_code


__all__ = [
    "ADAPTERS",
    "ENV_FILE",
    "EXIT_CONFIG",
    "EXIT_ERROR",
    "EXIT_HEALTH_BLOCKED",
    "EXIT_OK",
    "LM_STUDIO_BASE_URL",
    "STAGING_DIRNAME",
    "AdapterEntry",
    "HealthDecision",
    "LmStudioStatus",
    "RunReport",
    "SourceRecord",
    "build_parser",
    "collapse_uid_collisions",
    "evaluate_health",
    "implemented_adapters",
    "is_runnable",
    "iter_sources",
    "load_dotenv",
    "main",
    "parse_env_file",
    "print_run_report",
    "print_validation",
    "probe_lm_studio",
    "process_source",
    "run_pipeline",
]
