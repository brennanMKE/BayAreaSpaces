"""``out/health.json`` — the per-run diagnostics, written every single run.

**Whether or not publication proceeded.** A blocked run is exactly when the
diagnostics are wanted: the calendar was not written, something decided that,
and the only artifact left to read at 09:00 is this file. Issue 0016 blocks and
returns early; this module runs on both paths.

What it is for
--------------------------------------------------------------------------

Two consumers, in order of importance:

1. **The repair workflow.** CLAUDE.md: when a source shows 0 for three
   consecutive nights, OpenCode is dispatched against *that one space* with
   ``raw/`` from those nights as the diff material. So the three-night condition
   is not something a consumer reconstructs by joining nightly files — it is
   :data:`REPAIR_ZERO_NIGHTS`, counted here from the run history the store
   already keeps, and published as ``repair.candidates`` with the raw paths of
   the nights in question attached. The dispatch is a read, not an analysis.
2. **The website project**, which may eventually surface a status page. Hence
   :data:`SCHEMA`: a consumer that pins a version can detect the day the shape
   changes instead of discovering it through a stack trace.

Both of them are served by the same question, the one CLAUDE.md says is always
asked first: **did the source change or did we?** ``raw_path`` is on every
source record for that reason — the answer is a ``diff`` of last night's
archived body against tonight's, and hunting for the file is most of the ten
seconds it is supposed to take.

Composition, not re-derivation
--------------------------------------------------------------------------

Nearly everything here already exists as ``as_dict()`` or ``summary()`` on the
object that owns the number:

============================  ==========================================
:class:`~pipeline.cli.RunReport`      timestamps, counts, carry-forward, alerts
:class:`~pipeline.cli.SourceRecord`   transport, counts, staleness, ``raw_path``
:class:`~pipeline.health.HealthVerdict`  gate outcomes, per-source verdicts
:class:`~pipeline.filters.FilterResult`  per-rule drop counts, dead patterns
:class:`~pipeline.dedupe.DedupeResult`   merge counts, near misses
:class:`~pipeline.emit_ics.EmitResult`   what was published, per space
============================  ==========================================

This module joins them and adds exactly two things neither of them can know
alone: the per-source gate verdict welded onto the per-source record (they are
built by different stages and a reader should not have to join two lists by
``space_id:label``), and the zero-night streak, which needs the store's run
history. Everything else is a key lookup.

The write
--------------------------------------------------------------------------

Atomic, through :func:`~pipeline.emit_ics.write_atomic` — the same temp-file
plus :func:`os.replace` the ICS emit uses, for the same reason: a status page
polling this file must see the old document or the new one, never a prefix of
the new one. A failure leaves the previous ``health.json`` exactly where it was.

``--dry-run`` writes to ``out/.staging/health.json``. A dry run that overwrote
the live diagnostics would destroy the record of the *real* run someone is
mid-way through diagnosing, which is the same trap as a dry run spending the
next real fetch's ETag. A ``--space`` run stages for the same reason it stages
its calendar: it holds one space's worth of the world.

Implemented by issue 0017.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pipeline import __version__
from pipeline.config import OUT_DIR
from pipeline.emit_ics import write_atomic

LOG = logging.getLogger("pipeline.health_json")

# --------------------------------------------------------------------------- knobs

#: The document version. Bump it when a key changes meaning or disappears;
#: adding a key is not a break and does not need one.
SCHEMA = 1

#: The file, under whichever ``out/`` the run is writing to.
HEALTH_FILENAME = "health.json"

#: Consecutive zero nights that mean an adapter has drifted rather than a space
#: having a quiet week. CLAUDE.md's number, and the trigger for the OpenCode
#: repair pass.
REPAIR_ZERO_NIGHTS = 3

#: How far back the zero streak is scanned. Well past the threshold, so a long
#: streak reports its true length rather than saturating at three.
ZERO_STREAK_LIMIT = 30

#: The one status in which ``horizon_count`` is a measurement of the feed.
#: ``skipped`` and ``blocked`` are zero by decision, ``failed``/``error`` are
#: carry-forward's clock, and ``not_modified`` has no body to count — a 304 is
#: the server saying the source is current, which is the *opposite* of drift.
#: See :meth:`pipeline.store.Store.zero_streak`.
JUDGED_STATUSES = frozenset({"ok"})


# --------------------------------------------------------------------------- zero nights


def zero_nights(
    record: Any,
    store: Any = None,
    *,
    started_at: dt.datetime | None = None,
    run_id: int | None = None,
    limit: int = ZERO_STREAK_LIMIT,
) -> list[dict[str, Any]]:
    """The unbroken streak of nights this source answered 0, newest first.

    Tonight comes from the live ``record`` and the rest from the store, rather
    than reading all of it back out of the database, because a ``--dry-run``
    never wrote a row for tonight and would otherwise report a streak that is
    one night short on the one invocation someone is using to investigate.
    ``run_id`` excludes tonight's row from the history half so it cannot be
    counted twice.
    """
    nights: list[dict[str, Any]] = []
    status = str(getattr(record, "status", "") or "")

    if status in JUDGED_STATUSES:
        if int(getattr(record, "horizon_count", 0) or 0) > 0:
            # Tonight is non-zero. Whatever happened before, the streak is over.
            return []
        nights.append(
            {
                "run_id": run_id,
                "started_at": started_at.isoformat() if started_at else None,
                "status": status,
                "horizon_count": int(getattr(record, "horizon_count", 0) or 0),
                "event_count": int(getattr(record, "event_count", 0) or 0),
                "raw_path": getattr(record, "raw_path", None),
            }
        )

    history = getattr(store, "zero_streak", None)
    if callable(history):
        nights.extend(
            row.as_dict()
            for row in history(
                str(getattr(record, "space_id", "") or ""),
                str(getattr(record, "label", "") or ""),
                exclude_run_id=run_id,
                limit=limit,
            )
        )
    return nights


# --------------------------------------------------------------------------- document


def _events_by_space(events: Sequence[Any]) -> dict[str, int]:
    """``{space_id: events}`` for the set that would be (or was) published."""
    totals: dict[str, int] = {}
    for event in events:
        space_id = getattr(event, "space_id", "") or ""
        totals[space_id] = totals.get(space_id, 0) + 1
    return dict(sorted(totals.items()))


def _llm_block(report: Any) -> dict[str, Any]:
    """Whether the model stages ran, and why not when they did not.

    ``ran`` is deliberately not ``lm_studio.available``: LM Studio answering
    ``/models`` means the *probe* succeeded, and enrich still does not exist
    (issue 0029). The honest answer today is ``False`` with the reason attached.
    """
    probe = getattr(report, "lm_studio", None)
    skipped = getattr(report, "enrich_skipped_reason", None)
    return {
        "ran": bool(probe is not None and probe.available and not skipped),
        "checked": bool(probe.checked) if probe is not None else False,
        "available": bool(probe.available) if probe is not None else False,
        "skipped_reason": skipped,
        "probe": probe.as_dict() if probe is not None else None,
    }


def build_health_document(
    report: Any,
    *,
    store: Any = None,
    path: Path | str | None = None,
    generated_at: dt.datetime | None = None,
) -> dict[str, Any]:
    """Assemble the document. Pure: no filesystem, no clock beyond an argument.

    Built on top of :meth:`pipeline.cli.RunReport.as_dict`, which already
    carries the run level. The two structural moves are lifting the per-source
    lists out of it — the records and the gate verdicts are separate lists in
    separate objects and belong welded together in one row per source — and
    adding the zero-night streak the repair workflow dispatches on.
    """
    run = report.as_dict()
    records = {record.key: record for record in getattr(report, "records", ()) or ()}

    # Per-source: the record (transport, counts, raw_path, filters) merged with
    # this run's gate verdict. Both lists come out of ``run`` so neither is
    # serialized twice.
    verdicts = {
        verdict["key"]: verdict for verdict in run.get("health", {}).get("sources", [])
    }
    gates = dict(run.pop("health", {}))
    gates.pop("sources", None)

    started_at = getattr(report, "started_at", None)
    run_id = getattr(report, "run_id", None)

    sources: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for entry in run.pop("sources", []):
        key = f"{entry['space_id']}:{entry['label']}"
        verdict = verdicts.get(key)
        record = records.get(key)
        nights = (
            zero_nights(record, store, started_at=started_at, run_id=run_id)
            if record is not None
            else []
        )
        streak = len(nights)
        repair_ready = streak >= REPAIR_ZERO_NIGHTS

        entry = dict(entry)
        entry["key"] = key
        # Hoisted out of ``gate`` so the three questions a human asks first —
        # what did it return, what did it return last night, what did the gates
        # make of it — are readable without descending into the verdict.
        entry["previous_count"] = verdict.get("previous_count") if verdict else None
        entry["gate_outcome"] = verdict.get("outcome") if verdict else None
        entry["gate"] = verdict
        entry["consecutive_zero_nights"] = streak
        entry["repair_ready"] = repair_ready
        entry["zero_nights"] = nights
        sources.append(entry)

        if repair_ready:
            candidates.append(
                {
                    "space_id": entry["space_id"],
                    "label": entry["label"],
                    "key": key,
                    "adapter": entry["adapter"],
                    "url": entry["url"],
                    "consecutive_zero_nights": streak,
                    # The diff material, newest first. This is the whole reason
                    # ``raw/`` is retained and kept for 30 days.
                    "raw_paths": [
                        night["raw_path"] for night in nights if night.get("raw_path")
                    ],
                }
            )

    events = getattr(report, "events", ()) or ()
    return {
        "schema": SCHEMA,
        "pipeline_version": __version__,
        "generated_at": (generated_at or dt.datetime.now(dt.timezone.utc)).isoformat(),
        "path": str(path) if path is not None else None,
        # First, because it is the reason this file exists and the only part a
        # consumer is expected to act on unprompted.
        "repair": {
            "zero_night_threshold": REPAIR_ZERO_NIGHTS,
            "candidate_count": len(candidates),
            "candidates": candidates,
        },
        "run": run,
        "totals": {
            "events": getattr(report, "event_count", 0),
            # Per space, from the set that was published (or would have been).
            # Not from ``emit``: a blocked run has no emit summary, and that is
            # the run whose per-space totals someone most wants to see.
            "by_space": _events_by_space(events),
            "spaces": run.get("counts", {}).get("spaces", 0),
            "sources": run.get("counts", {}).get("sources", 0),
            "gate_outcomes": gates.get("counts", {}),
            "repair_candidates": len(candidates),
        },
        "llm": _llm_block(report),
        "gates": gates,
        "sources": sources,
    }


# --------------------------------------------------------------------------- write


def health_json_path(out_dir: Path | str | None = None) -> Path:
    return Path(out_dir if out_dir is not None else OUT_DIR) / HEALTH_FILENAME


def write_health_json(
    report: Any,
    *,
    store: Any = None,
    out_dir: Path | str | None = None,
    generated_at: dt.datetime | None = None,
) -> Path:
    """Serialize and atomically write the document. Returns the path written.

    ``out_dir`` defaults to the run's own output directory, which is already
    ``out/.staging/`` for a dry run or a single-space run — the staging decision
    is made once, in :func:`pipeline.cli.run_pipeline`, and this follows it
    rather than deciding again and disagreeing.
    """
    target = out_dir if out_dir is not None else getattr(report, "out_dir", None)
    path = health_json_path(target)
    document = build_health_document(
        report, store=store, path=path, generated_at=generated_at
    )
    payload = json.dumps(document, indent=2, sort_keys=False, default=str) + "\n"
    write_atomic(path, payload.encode("utf-8"))
    LOG.info(
        "wrote %s (%d sources, %d repair candidates)",
        path,
        len(document["sources"]),
        document["repair"]["candidate_count"],
    )
    return path


__all__ = [
    "HEALTH_FILENAME",
    "JUDGED_STATUSES",
    "REPAIR_ZERO_NIGHTS",
    "SCHEMA",
    "ZERO_STREAK_LIMIT",
    "build_health_document",
    "health_json_path",
    "write_health_json",
    "zero_nights",
]
