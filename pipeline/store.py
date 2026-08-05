"""The local working store: ``db/events.sqlite``.

Per ``CLAUDE.md``, SQLite holds everything that is per-run bookkeeping; Postgres
on EC2 holds only the published result. Three concerns live here, and nothing
else should be added without a reason that survives the same sentence:

**Per-source HTTP state.** The real implementation of :class:`pipeline.fetch.HttpStateStore`,
keyed on ``{space_id}:{label}``. Dropping this in behind the protocol is what
makes conditional GET actually work between runs — the in-memory default from
issue 0006 forgets every ETag when the process exits, which for the two feeds in
this registry that are 8 MB and 11 MB means re-downloading 19 MB every night for
nothing.

**Event history.** Every event ever seen, by ``uid``, with ``first_seen``,
``last_seen``, ``content_hash`` and the serialized :class:`~pipeline.normalize.Event`
itself. The serialized copy is not redundancy: issue 0014 has to *republish* a
failed source's last-known-good events, and it cannot reconstruct them from a
count.

**Run history.** Per-run, per-source counts, latency, HTTP status and gate
outcomes, for the night-over-night comparison in issue 0016 and for
``require_nonzero_once``.

Two invariants this module exists to hold
--------------------------------------------------------------------------

**``first_seen`` survives a content change.** If a space edits a title, that is
not a new event. ``content_hash`` changes; ``first_seen`` does not. This is the
field RSS ``pubDate`` uses (issue 0018), so getting it wrong re-notifies every
subscriber about every event. :meth:`Store.record_events` merges on ``uid`` and
never lets an incoming ``first_seen`` overwrite a stored one.

**No naive datetime is ever stored.** CLAUDE.md's timezone invariant applies to
the database too. Everything is written as an ISO-8601 UTC string, and
:func:`to_iso` raises :class:`NaiveDatetimeError` rather than guessing a zone.
A naive value in a cache is worse than one in a calculation, because it is still
wrong six months later.

``require_nonzero_once``
--------------------------------------------------------------------------

"Has this source *ever* returned events?" is a different question from "did it
return zero tonight?", and only the first one can be answered from persistent
state. Humanmade's Eventbrite organizer and Hacker Dojo's Luma page are both
legitimately at zero right now, so a "went to zero" gate can never fire for them
while a naive alert fires nightly. :meth:`Store.has_ever_returned_events` is
that question as a first-class query, backed by a milestone row that is updated
whenever a source produces a non-zero count — from either
:meth:`Store.record_events` or :meth:`Store.record_source_run`.

Schema migrations
--------------------------------------------------------------------------

Versioned, with an explicit upgrade path, so adding a column later does not mean
deleting the database. This runs unattended under launchd; a schema surprise at
03:15 is expensive. A database written by a *newer* build than the code opening
it is refused loudly rather than half-read.

Issue 0013.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from pipeline.config import DB_DIR
from pipeline.fetch import SourceState
from pipeline.normalize import AddressSource, Event, QuarantineReason

# --------------------------------------------------------------------------- knobs

#: The nightly job's working store. Gitignored; created on demand.
DEFAULT_DB_PATH = DB_DIR / "events.sqlite"

#: Bump this and add a migration below. Never edit an existing migration.
SCHEMA_VERSION = 1

#: sqlite3 in-memory marker, accepted anywhere a path is.
MEMORY = ":memory:"


# --------------------------------------------------------------------------- errors


class StoreError(Exception):
    """Base class for every failure raised by this module."""


class NaiveDatetimeError(StoreError):
    """A naive datetime was handed to the store.

    Deliberately not coerced. CLAUDE.md: "Never let a naive datetime past
    ``normalize.py``. Parse to aware immediately, store UTC." Guessing a zone
    here would bury the bug in a row that outlives the run that wrote it.
    """


class SchemaTooNewError(StoreError):
    """The database was written by a newer build than the one opening it."""


# --------------------------------------------------------------------------- time


def utcnow() -> dt.datetime:
    """The current instant, aware, in UTC."""
    return dt.datetime.now(dt.timezone.utc)


def to_iso(value: dt.datetime | None, *, field: str = "datetime") -> str | None:
    """An aware datetime as an ISO-8601 UTC string, or ``None``.

    Raises :class:`NaiveDatetimeError` for a naive datetime or a non-datetime.
    """
    if value is None:
        return None
    if not isinstance(value, dt.datetime):
        raise NaiveDatetimeError(
            f"{field} is {type(value).__name__}, not a datetime; the store only "
            "accepts aware datetimes"
        )
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise NaiveDatetimeError(
            f"{field} is naive ({value.isoformat()}). The store writes ISO-8601 "
            "UTC and will not guess a zone — see the timezone invariant in "
            "CLAUDE.md."
        )
    return value.astimezone(dt.timezone.utc).isoformat()


def from_iso(text: str | None, *, field: str = "datetime") -> dt.datetime | None:
    """Parse a stored ISO-8601 string back to an aware UTC datetime."""
    if text is None or text == "":
        return None
    try:
        value = dt.datetime.fromisoformat(text)
    except ValueError as exc:  # pragma: no cover - only a corrupted row
        raise StoreError(f"{field} is not ISO-8601: {text!r}") from exc
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise NaiveDatetimeError(
            f"{field} was stored without a timezone ({text!r}); the row predates "
            "the invariant or was written by something else"
        )
    return value.astimezone(dt.timezone.utc)


def _date_from_iso(text: str | None) -> dt.date | None:
    if not text:
        return None
    return dt.date.fromisoformat(text)


# --------------------------------------------------------------------------- rows


@dataclass(frozen=True)
class StoredEvent:
    """One row of event history, with the :class:`Event` reconstructed."""

    uid: str
    space_id: str
    source_label: str
    title: str
    start_utc: dt.datetime | None
    first_seen: dt.datetime
    last_seen: dt.datetime
    content_hash: str
    last_changed_at: dt.datetime
    event: Event

    @property
    def source_key(self) -> str:
        return f"{self.space_id}:{self.source_label}"


@dataclass(frozen=True)
class EventMerge:
    """What :meth:`Store.record_events` did, and the reconciled events.

    :attr:`events` carry the **stored** ``first_seen`` rather than the run
    timestamp normalize put there, so the caller can hand them straight to RSS
    (issue 0018) without re-reading the table.
    """

    events: tuple[Event, ...] = ()
    new_uids: tuple[str, ...] = ()
    changed_uids: tuple[str, ...] = ()
    unchanged_uids: tuple[str, ...] = ()
    recorded_at: dt.datetime | None = None

    @property
    def count(self) -> int:
        return len(self.events)

    @property
    def new_count(self) -> int:
        return len(self.new_uids)

    @property
    def changed_count(self) -> int:
        return len(self.changed_uids)

    @property
    def unchanged_count(self) -> int:
        return len(self.unchanged_uids)


@dataclass(frozen=True)
class RunRow:
    """One row of run history."""

    run_id: int
    started_at: dt.datetime
    finished_at: dt.datetime | None = None
    dry_run: bool = False
    space_filter: str | None = None
    horizon_days: int = 0
    event_count: int = 0
    source_count: int = 0
    published: bool = False
    health_blocked: bool = False
    health_reasons: tuple[str, ...] = ()
    exit_code: int | None = None
    version: str | None = None

    @property
    def elapsed_seconds(self) -> float | None:
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()


@dataclass(frozen=True)
class SourceRunRow:
    """One source's contribution to one run.

    ``raw_count`` and ``horizon_count`` stay apart for the reason CLAUDE.md
    gives: Sequoia Fabrica ships 89 VEVENTs for ~7 live events and Maker Nexus
    3645 for 171, so the first number says almost nothing. Gates read
    ``horizon_count``.
    """

    run_id: int
    space_id: str
    label: str
    adapter: str = ""
    status: str = ""
    http_status: int | None = None
    content_type: str | None = None
    bytes: int = 0
    conditional: bool = False
    attempts: int = 0
    raw_count: int = 0
    horizon_count: int = 0
    normalized_count: int = 0
    event_count: int = 0
    dropped_count: int = 0
    quarantined_count: int = 0
    filtered_out_count: int = 0
    fetch_seconds: float = 0.0
    elapsed_seconds: float = 0.0
    gate_blocked: bool = False
    gate_reasons: tuple[str, ...] = ()
    problem: str | None = None
    reason: str | None = None
    error: str | None = None
    started_at: dt.datetime | None = None

    @property
    def source_key(self) -> str:
        return f"{self.space_id}:{self.label}"


# --------------------------------------------------------------------------- Event codec


def event_from_dict(data: dict[str, Any]) -> Event:
    """Rebuild an :class:`Event` from :meth:`Event.as_dict` output.

    The inverse of the wire form, and the reason carry-forward (issue 0014) can
    republish a failed source's events instead of dropping the space out of the
    calendar. Datetimes come back aware — :class:`Event` refuses them otherwise.
    """
    fields = dict(data)
    for key in ("start_utc", "end_utc", "first_seen", "last_seen"):
        fields[key] = from_iso(fields.get(key), field=key)
    for key in ("start_date", "end_date"):
        fields[key] = _date_from_iso(fields.get(key))
    fields["categories"] = tuple(fields.get("categories") or ())
    fields["address_source"] = AddressSource(fields.get("address_source") or "none")
    quarantine = fields.get("quarantine")
    fields["quarantine"] = QuarantineReason(quarantine) if quarantine else None
    known = {f for f in Event.__dataclass_fields__}
    return Event(**{k: v for k, v in fields.items() if k in known})


# --------------------------------------------------------------------------- schema

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS source_state (
    source_key           TEXT PRIMARY KEY,
    etag                 TEXT,
    last_modified        TEXT,
    last_status          INTEGER,
    last_success_at      TEXT,
    last_seen            TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_content_hash    TEXT
);

CREATE TABLE IF NOT EXISTS event (
    uid             TEXT PRIMARY KEY,
    space_id        TEXT NOT NULL,
    source_label    TEXT NOT NULL,
    source_key      TEXT NOT NULL,
    title           TEXT NOT NULL DEFAULT '',
    start_utc       TEXT,
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL,
    last_changed_at TEXT NOT NULL,
    content_hash    TEXT NOT NULL DEFAULT '',
    run_id          INTEGER,
    payload         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS event_source_key_idx ON event (source_key);
CREATE INDEX IF NOT EXISTS event_last_seen_idx  ON event (source_key, last_seen);

CREATE TABLE IF NOT EXISTS run (
    run_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    dry_run        INTEGER NOT NULL DEFAULT 0,
    space_filter   TEXT,
    horizon_days   INTEGER NOT NULL DEFAULT 0,
    event_count    INTEGER NOT NULL DEFAULT 0,
    published      INTEGER NOT NULL DEFAULT 0,
    health_blocked INTEGER NOT NULL DEFAULT 0,
    health_reasons TEXT NOT NULL DEFAULT '[]',
    exit_code      INTEGER,
    version        TEXT
);
CREATE INDEX IF NOT EXISTS run_started_idx ON run (started_at);

CREATE TABLE IF NOT EXISTS run_source (
    run_id             INTEGER NOT NULL REFERENCES run (run_id) ON DELETE CASCADE,
    space_id           TEXT NOT NULL,
    label              TEXT NOT NULL,
    source_key         TEXT NOT NULL,
    adapter            TEXT NOT NULL DEFAULT '',
    status             TEXT NOT NULL DEFAULT '',
    http_status        INTEGER,
    content_type       TEXT,
    bytes              INTEGER NOT NULL DEFAULT 0,
    conditional        INTEGER NOT NULL DEFAULT 0,
    attempts           INTEGER NOT NULL DEFAULT 0,
    raw_count          INTEGER NOT NULL DEFAULT 0,
    horizon_count      INTEGER NOT NULL DEFAULT 0,
    normalized_count   INTEGER NOT NULL DEFAULT 0,
    event_count        INTEGER NOT NULL DEFAULT 0,
    dropped_count      INTEGER NOT NULL DEFAULT 0,
    quarantined_count  INTEGER NOT NULL DEFAULT 0,
    filtered_out_count INTEGER NOT NULL DEFAULT 0,
    fetch_seconds      REAL NOT NULL DEFAULT 0.0,
    elapsed_seconds    REAL NOT NULL DEFAULT 0.0,
    gate_blocked       INTEGER NOT NULL DEFAULT 0,
    gate_reasons       TEXT NOT NULL DEFAULT '[]',
    problem            TEXT,
    reason             TEXT,
    error              TEXT,
    PRIMARY KEY (run_id, space_id, label)
);
CREATE INDEX IF NOT EXISTS run_source_key_idx ON run_source (source_key, run_id);

CREATE TABLE IF NOT EXISTS source_milestone (
    source_key           TEXT PRIMARY KEY,
    first_nonzero_at     TEXT,
    first_nonzero_run_id INTEGER,
    last_nonzero_at      TEXT,
    max_event_count      INTEGER NOT NULL DEFAULT 0,
    nonzero_runs         INTEGER NOT NULL DEFAULT 0
);
"""


def _migrate_to_1(conn: sqlite3.Connection) -> None:
    """Create the initial schema. Never edit this — add a v2 migration."""
    conn.executescript(_SCHEMA_V1)


#: target version -> the function that brings a database up to it.
_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    1: _migrate_to_1,
}


# --------------------------------------------------------------------------- store


class Store:
    """The working store. Implements :class:`pipeline.fetch.HttpStateStore`.

    One instance per run. It is a cache and an audit trail, not the product, so
    the schema is boring on purpose and everything reads back as plain data.
    """

    def __init__(
        self,
        path: Path | str = DEFAULT_DB_PATH,
        *,
        now: Callable[[], dt.datetime] | None = None,
        create: bool = True,
    ) -> None:
        self.path = Path(path) if str(path) != MEMORY else MEMORY
        self._now = now or utcnow
        if self.path != MEMORY and create:
            # db/ is gitignored and does not exist on a fresh checkout.
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self.path != MEMORY:
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self.migrated_from, self.schema_version = self._migrate()

    # -- lifecycle -------------------------------------------------------------

    @classmethod
    def in_memory(cls, **kwargs: Any) -> Store:
        """A throwaway store. Tests and ``--dry-run`` experiments."""
        return cls(MEMORY, **kwargs)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @property
    def connection(self) -> sqlite3.Connection:
        """The underlying connection. For diagnostics, not for writing."""
        return self._conn

    # -- migrations ------------------------------------------------------------

    def _migrate(self) -> tuple[int, int]:
        """Bring the database up to :data:`SCHEMA_VERSION`. Idempotent.

        Returns ``(version_found, version_now)``. Running twice is a no-op —
        the second call finds the version row already at the target and applies
        nothing.
        """
        conn = self._conn
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            "  version INTEGER PRIMARY KEY,"
            "  applied_at TEXT NOT NULL"
            ")"
        )
        row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        found = int(row["v"] or 0)
        if found > SCHEMA_VERSION:
            raise SchemaTooNewError(
                f"{self.path} is at schema version {found}, but this build only "
                f"knows {SCHEMA_VERSION}. Refusing to read it — a half-understood "
                "schema at 03:15 is worse than a failed run."
            )
        stamp = to_iso(self._now())
        for target in range(found + 1, SCHEMA_VERSION + 1):
            _MIGRATIONS[target](conn)
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (target, stamp),
            )
        conn.commit()
        return found, SCHEMA_VERSION

    def applied_migrations(self) -> list[int]:
        """Every schema version this database has been through, in order."""
        rows = self._conn.execute(
            "SELECT version FROM schema_version ORDER BY version"
        ).fetchall()
        return [int(row["version"]) for row in rows]

    # -- HttpStateStore --------------------------------------------------------

    def get(self, key: str) -> SourceState | None:
        """The stored HTTP state for ``key``, or ``None`` if never fetched."""
        row = self._conn.execute(
            "SELECT * FROM source_state WHERE source_key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        return SourceState(
            etag=row["etag"],
            last_modified=row["last_modified"],
            last_status=row["last_status"],
            last_success_at=from_iso(row["last_success_at"], field="last_success_at"),
            last_seen=from_iso(row["last_seen"], field="last_seen"),
            consecutive_failures=int(row["consecutive_failures"] or 0),
            last_content_hash=row["last_content_hash"],
        )

    def put(self, key: str, state: SourceState) -> None:
        """Persist ``state`` under ``key``, replacing anything already there."""
        self._conn.execute(
            """
            INSERT INTO source_state (
                source_key, etag, last_modified, last_status,
                last_success_at, last_seen, consecutive_failures, last_content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (source_key) DO UPDATE SET
                etag = excluded.etag,
                last_modified = excluded.last_modified,
                last_status = excluded.last_status,
                last_success_at = excluded.last_success_at,
                last_seen = excluded.last_seen,
                consecutive_failures = excluded.consecutive_failures,
                last_content_hash = excluded.last_content_hash
            """,
            (
                key,
                state.etag,
                state.last_modified,
                state.last_status,
                to_iso(state.last_success_at, field=f"{key}.last_success_at"),
                to_iso(state.last_seen, field=f"{key}.last_seen"),
                int(state.consecutive_failures or 0),
                state.last_content_hash,
            ),
        )
        self._conn.commit()

    def source_states(self) -> dict[str, SourceState]:
        """Every source's HTTP state, keyed by ``{space_id}:{label}``."""
        rows = self._conn.execute("SELECT source_key FROM source_state").fetchall()
        states: dict[str, SourceState] = {}
        for row in rows:
            state = self.get(row["source_key"])
            if state is not None:
                states[row["source_key"]] = state
        return states

    def forget_source_state(self, key: str) -> None:
        """Drop the stored validators so the next GET is unconditional.

        The repair hatch for the case where a server hands out a stale ETag: a
        304 forever is indistinguishable from a healthy feed until someone
        notices the calendar stopped moving.
        """
        self._conn.execute("DELETE FROM source_state WHERE source_key = ?", (key,))
        self._conn.commit()

    # -- event history ---------------------------------------------------------

    def record_events(
        self,
        events: Iterable[Event],
        *,
        run_id: int | None = None,
        now: dt.datetime | None = None,
    ) -> EventMerge:
        """Merge ``events`` into history on ``uid`` and return what changed.

        **``first_seen`` is written once and never again.** A stored row keeps
        the ``first_seen`` it was created with even when ``content_hash``
        changes, because a space editing a title is not a new event and a
        subscriber must not be re-notified. Issue 0018 reads that column as RSS
        ``pubDate``; overwriting it re-notifies everyone.

        Every event in one call shares a single ``last_seen`` timestamp, which
        is what makes "the last run that produced events" a single value to
        select on in :meth:`carry_forward_events`.
        """
        stamp = now if now is not None else self._now()
        seen_iso = to_iso(stamp, field="record_events(now=)")
        assert seen_iso is not None

        reconciled: list[Event] = []
        new_uids: list[str] = []
        changed_uids: list[str] = []
        unchanged_uids: list[str] = []
        per_source: dict[str, int] = {}

        for event in events:
            row = self._conn.execute(
                "SELECT first_seen, content_hash, last_changed_at FROM event WHERE uid = ?",
                (event.uid,),
            ).fetchone()

            if row is None:
                first_seen = event.first_seen or stamp
                first_iso = to_iso(first_seen, field=f"{event.uid}.first_seen")
                changed_iso = seen_iso
                new_uids.append(event.uid)
            else:
                # The stored first_seen wins, always. A title edit changes
                # content_hash and nothing else; re-dating the event here would
                # re-notify every subscriber (issue 0018 reads it as pubDate).
                first_iso = row["first_seen"]
                first_seen = from_iso(first_iso, field=f"{event.uid}.first_seen")
                if row["content_hash"] != event.content_hash:
                    changed_uids.append(event.uid)
                    changed_iso = seen_iso
                else:
                    unchanged_uids.append(event.uid)
                    changed_iso = row["last_changed_at"] or seen_iso

            merged = replace(event, first_seen=first_seen, last_seen=stamp)
            reconciled.append(merged)

            values = (
                merged.space_id,
                merged.source_label,
                f"{merged.space_id}:{merged.source_label}",
                merged.title,
                to_iso(merged.start_utc, field=f"{merged.uid}.start_utc"),
                seen_iso,
                changed_iso,
                merged.content_hash,
                run_id,
                json.dumps(merged.as_dict(), ensure_ascii=False),
            )
            if row is None:
                self._conn.execute(
                    """
                    INSERT INTO event (
                        uid, first_seen, space_id, source_label, source_key,
                        title, start_utc, last_seen, last_changed_at,
                        content_hash, run_id, payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (merged.uid, first_iso, *values),
                )
            else:
                # first_seen is deliberately absent from this UPDATE.
                self._conn.execute(
                    """
                    UPDATE event SET
                        space_id = ?, source_label = ?, source_key = ?,
                        title = ?, start_utc = ?, last_seen = ?,
                        last_changed_at = ?, content_hash = ?, run_id = ?,
                        payload = ?
                    WHERE uid = ?
                    """,
                    (*values, merged.uid),
                )
            key = f"{merged.space_id}:{merged.source_label}"
            per_source[key] = per_source.get(key, 0) + 1

        for key, count in per_source.items():
            self._note_nonzero(key, count, seen_iso, run_id)

        self._conn.commit()
        return EventMerge(
            events=tuple(reconciled),
            new_uids=tuple(new_uids),
            changed_uids=tuple(changed_uids),
            unchanged_uids=tuple(unchanged_uids),
            recorded_at=stamp,
        )

    def get_event(self, uid: str) -> StoredEvent | None:
        """One event's history row, with the :class:`Event` rebuilt."""
        row = self._conn.execute("SELECT * FROM event WHERE uid = ?", (uid,)).fetchone()
        return self._stored_event(row) if row is not None else None

    def first_seen(self, uid: str) -> dt.datetime | None:
        """When this ``uid`` was first recorded. RSS ``pubDate``, issue 0018."""
        row = self._conn.execute(
            "SELECT first_seen FROM event WHERE uid = ?", (uid,)
        ).fetchone()
        if row is None:
            return None
        return from_iso(row["first_seen"], field=f"{uid}.first_seen")

    def event_count(self) -> int:
        """How many distinct events have ever been seen."""
        row = self._conn.execute("SELECT COUNT(*) AS n FROM event").fetchone()
        return int(row["n"])

    def events_for_source(self, space_id: str, label: str) -> list[StoredEvent]:
        """Every event ever recorded for one source, newest start first."""
        rows = self._conn.execute(
            "SELECT * FROM event WHERE source_key = ? ORDER BY start_utc",
            (f"{space_id}:{label}",),
        ).fetchall()
        return [self._stored_event(row) for row in rows]

    def last_known_good_at(self, space_id: str, label: str) -> dt.datetime | None:
        """When this source last produced events, or ``None`` if never."""
        row = self._conn.execute(
            "SELECT MAX(last_seen) AS t FROM event WHERE source_key = ?",
            (f"{space_id}:{label}",),
        ).fetchone()
        if row is None or row["t"] is None:
            return None
        return from_iso(row["t"], field="last_seen")

    def carry_forward_events(self, space_id: str, label: str) -> list[Event]:
        """This source's last-known-good event set, ready to republish.

        CLAUDE.md: "A failed source carries forward its previous events. A
        transient 503 must never silently delete a space from the calendar."
        Issue 0014 is the caller. The set is the one from the most recent run
        that actually produced events — not the union of everything ever seen,
        which would resurrect events the source deliberately removed.
        """
        source_key = f"{space_id}:{label}"
        row = self._conn.execute(
            "SELECT MAX(last_seen) AS t FROM event WHERE source_key = ?",
            (source_key,),
        ).fetchone()
        if row is None or row["t"] is None:
            return []
        rows = self._conn.execute(
            "SELECT * FROM event WHERE source_key = ? AND last_seen = ? "
            "ORDER BY start_utc, uid",
            (source_key, row["t"]),
        ).fetchall()
        return [self._stored_event(r).event for r in rows]

    def _stored_event(self, row: sqlite3.Row) -> StoredEvent:
        event = event_from_dict(json.loads(row["payload"]))
        first_seen = from_iso(row["first_seen"], field="first_seen")
        last_seen = from_iso(row["last_seen"], field="last_seen")
        assert first_seen is not None and last_seen is not None
        # The columns are the authority, not the payload: a payload written
        # before a later merge would carry a stale last_seen.
        event = replace(event, first_seen=first_seen, last_seen=last_seen)
        changed = from_iso(row["last_changed_at"], field="last_changed_at")
        return StoredEvent(
            uid=row["uid"],
            space_id=row["space_id"],
            source_label=row["source_label"],
            title=row["title"],
            start_utc=from_iso(row["start_utc"], field="start_utc"),
            first_seen=first_seen,
            last_seen=last_seen,
            content_hash=row["content_hash"],
            last_changed_at=changed or first_seen,
            event=event,
        )

    # -- run history -----------------------------------------------------------

    def start_run(
        self,
        started_at: dt.datetime | None = None,
        *,
        dry_run: bool = False,
        space_filter: str | None = None,
        horizon_days: int = 0,
        version: str | None = None,
    ) -> int:
        """Open a run row and return its ``run_id``."""
        stamp = started_at if started_at is not None else self._now()
        cursor = self._conn.execute(
            "INSERT INTO run (started_at, dry_run, space_filter, horizon_days, version) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                to_iso(stamp, field="started_at"),
                int(bool(dry_run)),
                space_filter,
                int(horizon_days or 0),
                version,
            ),
        )
        self._conn.commit()
        return int(cursor.lastrowid or 0)

    def finish_run(
        self,
        run_id: int,
        *,
        finished_at: dt.datetime | None = None,
        event_count: int = 0,
        published: bool = False,
        health_blocked: bool = False,
        health_reasons: Sequence[str] = (),
        exit_code: int | None = None,
    ) -> None:
        """Close a run row with its totals and the health verdict."""
        stamp = finished_at if finished_at is not None else self._now()
        self._conn.execute(
            "UPDATE run SET finished_at = ?, event_count = ?, published = ?, "
            "health_blocked = ?, health_reasons = ?, exit_code = ? WHERE run_id = ?",
            (
                to_iso(stamp, field="finished_at"),
                int(event_count or 0),
                int(bool(published)),
                int(bool(health_blocked)),
                json.dumps(list(health_reasons)),
                exit_code,
                run_id,
            ),
        )
        self._conn.commit()

    def record_source_run(
        self,
        run_id: int,
        record: Any,
        *,
        gate_blocked: bool = False,
        gate_reasons: Sequence[str] = (),
    ) -> None:
        """Record one source's contribution to ``run_id``.

        ``record`` is a :class:`pipeline.cli.SourceRecord` — taken structurally
        rather than by import so this module stays underneath the CLI and the
        two cannot form a cycle.
        """
        space_id = getattr(record, "space_id", "")
        label = getattr(record, "label", "")
        key = f"{space_id}:{label}"
        event_count = int(getattr(record, "event_count", 0) or 0)
        self._conn.execute(
            """
            INSERT INTO run_source (
                run_id, space_id, label, source_key, adapter, status,
                http_status, content_type, bytes, conditional, attempts,
                raw_count, horizon_count, normalized_count, event_count,
                dropped_count, quarantined_count, filtered_out_count,
                fetch_seconds, elapsed_seconds, gate_blocked, gate_reasons,
                problem, reason, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (run_id, space_id, label) DO UPDATE SET
                adapter = excluded.adapter,
                status = excluded.status,
                http_status = excluded.http_status,
                content_type = excluded.content_type,
                bytes = excluded.bytes,
                conditional = excluded.conditional,
                attempts = excluded.attempts,
                raw_count = excluded.raw_count,
                horizon_count = excluded.horizon_count,
                normalized_count = excluded.normalized_count,
                event_count = excluded.event_count,
                dropped_count = excluded.dropped_count,
                quarantined_count = excluded.quarantined_count,
                filtered_out_count = excluded.filtered_out_count,
                fetch_seconds = excluded.fetch_seconds,
                elapsed_seconds = excluded.elapsed_seconds,
                gate_blocked = excluded.gate_blocked,
                gate_reasons = excluded.gate_reasons,
                problem = excluded.problem,
                reason = excluded.reason,
                error = excluded.error
            """,
            (
                run_id,
                space_id,
                label,
                key,
                getattr(record, "adapter", "") or "",
                getattr(record, "status", "") or "",
                getattr(record, "http_status", None),
                getattr(record, "content_type", None),
                int(getattr(record, "bytes", 0) or 0),
                int(bool(getattr(record, "conditional", False))),
                int(getattr(record, "attempts", 0) or 0),
                int(getattr(record, "raw_count", 0) or 0),
                int(getattr(record, "horizon_count", 0) or 0),
                int(getattr(record, "normalized_count", 0) or 0),
                event_count,
                int(getattr(record, "dropped_count", 0) or 0),
                int(getattr(record, "quarantined_count", 0) or 0),
                int(getattr(record, "filtered_out_count", 0) or 0),
                float(getattr(record, "fetch_seconds", 0.0) or 0.0),
                float(getattr(record, "elapsed_seconds", 0.0) or 0.0),
                int(bool(gate_blocked)),
                json.dumps(list(gate_reasons)),
                getattr(record, "problem", None),
                getattr(record, "reason", None),
                getattr(record, "error", None),
            ),
        )
        if event_count > 0:
            row = self._conn.execute(
                "SELECT started_at FROM run WHERE run_id = ?", (run_id,)
            ).fetchone()
            when = row["started_at"] if row is not None else to_iso(self._now())
            self._note_nonzero(key, event_count, when or "", run_id)
        self._conn.commit()

    def record_run(self, report: Any) -> int:
        """Record a whole :class:`pipeline.cli.RunReport` in one call.

        Structural again, for the same no-cycle reason as
        :meth:`record_source_run`.
        """
        health = getattr(report, "health", None)
        emit = getattr(report, "emit", None)
        run_id = self.start_run(
            getattr(report, "started_at", None),
            dry_run=bool(getattr(report, "dry_run", False)),
            space_filter=getattr(report, "space_filter", None),
            horizon_days=int(getattr(report, "horizon_days", 0) or 0),
            version=getattr(report, "version", None),
        )
        for record in getattr(report, "records", ()) or ():
            self.record_source_run(run_id, record)
        self.finish_run(
            run_id,
            finished_at=getattr(report, "finished_at", None) or self._now(),
            event_count=int(getattr(report, "event_count", 0) or 0),
            published=bool(getattr(report, "published", False)),
            health_blocked=bool(getattr(health, "blocked", False)),
            health_reasons=tuple(getattr(health, "reasons", ()) or ()),
            exit_code=getattr(report, "exit_code", None),
        )
        del emit  # reserved: issue 0017 may want the emit summary here too
        return run_id

    def set_gate_outcome(
        self,
        run_id: int,
        space_id: str,
        label: str,
        *,
        blocked: bool,
        reasons: Sequence[str] = (),
    ) -> None:
        """Attach issue 0016's gate verdict to an already-recorded source run."""
        self._conn.execute(
            "UPDATE run_source SET gate_blocked = ?, gate_reasons = ? "
            "WHERE run_id = ? AND space_id = ? AND label = ?",
            (int(bool(blocked)), json.dumps(list(reasons)), run_id, space_id, label),
        )
        self._conn.commit()

    def runs(self, limit: int = 10) -> list[RunRow]:
        """Run history, newest first."""
        rows = self._conn.execute(
            """
            SELECT run.*, (
                SELECT COUNT(*) FROM run_source WHERE run_source.run_id = run.run_id
            ) AS source_count
            FROM run ORDER BY run.run_id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [self._run_row(row) for row in rows]

    def latest_run(self) -> RunRow | None:
        """The most recent run, or ``None`` on a fresh database."""
        rows = self.runs(limit=1)
        return rows[0] if rows else None

    def get_run(self, run_id: int) -> RunRow | None:
        rows = self._conn.execute(
            """
            SELECT run.*, (
                SELECT COUNT(*) FROM run_source WHERE run_source.run_id = run.run_id
            ) AS source_count
            FROM run WHERE run.run_id = ?
            """,
            (run_id,),
        ).fetchall()
        return self._run_row(rows[0]) if rows else None

    def sources_for_run(self, run_id: int) -> list[SourceRunRow]:
        """Every source row for one run, in registry-ish (space, label) order."""
        rows = self._conn.execute(
            "SELECT run_source.*, run.started_at AS started_at FROM run_source "
            "JOIN run USING (run_id) WHERE run_id = ? ORDER BY space_id, label",
            (run_id,),
        ).fetchall()
        return [self._source_run_row(row) for row in rows]

    def source_history(
        self, space_id: str, label: str, limit: int = 10
    ) -> list[SourceRunRow]:
        """One source across the last ``limit`` runs, newest first.

        The night-over-night input for issue 0016 — including
        ``max_stale_days``, which needs more than one night to mean anything.
        """
        rows = self._conn.execute(
            "SELECT run_source.*, run.started_at AS started_at FROM run_source "
            "JOIN run USING (run_id) WHERE source_key = ? "
            "ORDER BY run_id DESC LIMIT ?",
            (f"{space_id}:{label}", limit),
        ).fetchall()
        return [self._source_run_row(row) for row in rows]

    def consecutive_failed_runs(
        self,
        space_id: str,
        label: str,
        *,
        statuses: Sequence[str] = ("failed", "error"),
        limit: int = 60,
    ) -> int:
        """How many *recorded* runs in a row this source failed, newest first.

        Issue 0014's escalation counter, and deliberately not the same number as
        :attr:`~pipeline.fetch.SourceState.consecutive_failures`. That one is
        incremented by the fetch layer and reset by a successful *transport*, so
        a feed answering 200 with the homepage every night — the wrong-adapter
        case CLAUDE.md calls as silent as a wrong URL — keeps it pinned at zero
        forever. This counts the run rows, which record the verdict after the
        adapter has had its say.

        **Dry runs do not count.** A dry run publishes nothing, and letting one
        push a source toward its third strike would make a debugging tool able to
        fire an alert.

        Runs in which this source was not touched (a ``--space`` run elsewhere)
        have no row and are simply absent from the scan rather than breaking the
        streak.
        """
        rows = self._conn.execute(
            "SELECT run_source.status AS status FROM run_source "
            "JOIN run USING (run_id) WHERE source_key = ? AND run.dry_run = 0 "
            "ORDER BY run_id DESC LIMIT ?",
            (f"{space_id}:{label}", int(limit)),
        ).fetchall()
        failing = set(statuses)
        streak = 0
        for row in rows:
            if row["status"] in failing:
                streak += 1
            else:
                break
        return streak

    def event_counts_for_run(self, run_id: int) -> dict[str, int]:
        """``{source_key: event_count}`` for one run. Issue 0016's comparison."""
        rows = self._conn.execute(
            "SELECT source_key, event_count FROM run_source WHERE run_id = ?",
            (run_id,),
        ).fetchall()
        return {row["source_key"]: int(row["event_count"]) for row in rows}

    def _run_row(self, row: sqlite3.Row) -> RunRow:
        started = from_iso(row["started_at"], field="started_at")
        assert started is not None
        return RunRow(
            run_id=int(row["run_id"]),
            started_at=started,
            finished_at=from_iso(row["finished_at"], field="finished_at"),
            dry_run=bool(row["dry_run"]),
            space_filter=row["space_filter"],
            horizon_days=int(row["horizon_days"] or 0),
            event_count=int(row["event_count"] or 0),
            source_count=int(row["source_count"] or 0),
            published=bool(row["published"]),
            health_blocked=bool(row["health_blocked"]),
            health_reasons=tuple(json.loads(row["health_reasons"] or "[]")),
            exit_code=row["exit_code"],
            version=row["version"],
        )

    def _source_run_row(self, row: sqlite3.Row) -> SourceRunRow:
        return SourceRunRow(
            run_id=int(row["run_id"]),
            space_id=row["space_id"],
            label=row["label"],
            adapter=row["adapter"],
            status=row["status"],
            http_status=row["http_status"],
            content_type=row["content_type"],
            bytes=int(row["bytes"] or 0),
            conditional=bool(row["conditional"]),
            attempts=int(row["attempts"] or 0),
            raw_count=int(row["raw_count"] or 0),
            horizon_count=int(row["horizon_count"] or 0),
            normalized_count=int(row["normalized_count"] or 0),
            event_count=int(row["event_count"] or 0),
            dropped_count=int(row["dropped_count"] or 0),
            quarantined_count=int(row["quarantined_count"] or 0),
            filtered_out_count=int(row["filtered_out_count"] or 0),
            fetch_seconds=float(row["fetch_seconds"] or 0.0),
            elapsed_seconds=float(row["elapsed_seconds"] or 0.0),
            gate_blocked=bool(row["gate_blocked"]),
            gate_reasons=tuple(json.loads(row["gate_reasons"] or "[]")),
            problem=row["problem"],
            reason=row["reason"],
            error=row["error"],
            started_at=from_iso(row["started_at"], field="started_at"),
        )

    # -- require_nonzero_once --------------------------------------------------

    def _note_nonzero(
        self, source_key: str, count: int, when: str, run_id: int | None
    ) -> None:
        """Record that ``source_key`` produced events. Idempotent per run.

        ``first_nonzero_at`` is written once, like ``first_seen`` and for the
        same reason: it is the answer to "has this source *ever* worked?", and
        an answer that moves is not an answer.
        """
        if count <= 0:
            return
        self._conn.execute(
            """
            INSERT INTO source_milestone (
                source_key, first_nonzero_at, first_nonzero_run_id,
                last_nonzero_at, max_event_count, nonzero_runs
            ) VALUES (?, ?, ?, ?, ?, 1)
            ON CONFLICT (source_key) DO UPDATE SET
                last_nonzero_at = excluded.last_nonzero_at,
                max_event_count = MAX(source_milestone.max_event_count, excluded.max_event_count),
                nonzero_runs = source_milestone.nonzero_runs + 1
            """,
            (source_key, when, run_id, when, int(count)),
        )

    def has_ever_returned_events(self, space_id: str, label: str) -> bool:
        """Has this source **ever** produced events? The ``require_nonzero_once`` gate.

        One of the four health-gate cases in CLAUDE.md, and the only one that
        cannot be answered from tonight's run alone. Humanmade's Eventbrite
        organizer and Hacker Dojo's Luma page sit at zero legitimately: "went to
        zero" can never fire for them, so a naive alert fires every night
        instead. Answering ``False`` here means "still never seen anything",
        which is a *waiting* state, not a failure.
        """
        row = self._conn.execute(
            "SELECT first_nonzero_at FROM source_milestone WHERE source_key = ?",
            (f"{space_id}:{label}",),
        ).fetchone()
        return row is not None and row["first_nonzero_at"] is not None

    def first_nonzero_at(self, space_id: str, label: str) -> dt.datetime | None:
        """When this source first produced events, or ``None`` if it never has."""
        row = self._conn.execute(
            "SELECT first_nonzero_at FROM source_milestone WHERE source_key = ?",
            (f"{space_id}:{label}",),
        ).fetchone()
        if row is None:
            return None
        return from_iso(row["first_nonzero_at"], field="first_nonzero_at")

    def sources_never_nonzero(self, keys: Iterable[str]) -> list[str]:
        """Which of ``keys`` have never returned an event. Order preserved."""
        never: list[str] = []
        for key in keys:
            space_id, _, label = key.partition(":")
            if not self.has_ever_returned_events(space_id, label):
                never.append(key)
        return never


# --------------------------------------------------------------------------- read-only


class ReadOnlyStoreError(StoreError):
    """A write was attempted through the ``--dry-run`` façade."""


class ReadOnlyStore:
    """A :class:`Store` that reads everything and writes nothing.

    What ``--dry-run`` fetches through. The full rationale is in
    :func:`pipeline.cli.run_pipeline`; the short version is that a dry run must
    behave *exactly* like a real one — same conditional GETs, same 304s, same
    carry-forward — while leaving the next real run's state untouched.

    :meth:`put` is a silent no-op because the fetch layer calls it on every
    single request and there is nothing for it to decide. The higher-level
    writers raise instead: the run loop is supposed to guard them, and a guard
    that has quietly stopped working should be a failed dry run rather than a
    polluted database.
    """

    #: Everything that would leave a mark. Refused, loudly.
    WRITE_METHODS = frozenset(
        {
            "record_events",
            "record_run",
            "record_source_run",
            "start_run",
            "finish_run",
            "set_gate_outcome",
            "forget_source_state",
        }
    )

    def __init__(self, store: Store) -> None:
        self._store = store

    @property
    def store(self) -> Store:
        """The wrapped store. For assertions, not for writing around the façade."""
        return self._store

    # -- HttpStateStore --------------------------------------------------------

    def get(self, key: str) -> SourceState | None:
        return self._store.get(key)

    def put(self, key: str, state: SourceState) -> None:
        """Deliberately nothing. A dry run must not spend the real run's ETag."""

    # -- everything else -------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        if name in ReadOnlyStore.WRITE_METHODS:
            raise ReadOnlyStoreError(
                f"{name}() was called on a read-only store. This is a --dry-run: "
                "it reads state so the run is realistic and writes none so the "
                "next real run is unaffected."
            )
        return getattr(self._store, name)

    def close(self) -> None:
        self._store.close()

    def __enter__(self) -> ReadOnlyStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


# --------------------------------------------------------------------------- helpers


def open_store(path: Path | str | None = None, **kwargs: Any) -> Store:
    """Open (creating if needed) the nightly working store.

    ``db/`` is gitignored and does not exist on a fresh checkout, so this
    creates it. Importing :mod:`pipeline.config` never touches the filesystem;
    the stage that writes is the stage that makes the directory.
    """
    return Store(DEFAULT_DB_PATH if path is None else path, **kwargs)


__all__ = [
    "DEFAULT_DB_PATH",
    "MEMORY",
    "SCHEMA_VERSION",
    "EventMerge",
    "NaiveDatetimeError",
    "ReadOnlyStore",
    "ReadOnlyStoreError",
    "RunRow",
    "SchemaTooNewError",
    "SourceRunRow",
    "Store",
    "StoreError",
    "StoredEvent",
    "event_from_dict",
    "from_iso",
    "open_store",
    "to_iso",
    "utcnow",
]
