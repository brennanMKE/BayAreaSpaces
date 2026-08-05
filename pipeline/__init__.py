"""Bay Area makerspace event pipeline.

The data-collection half of the aggregator: read ``sources.yaml``, fetch and
parse each space's event feed, normalize and dedupe, gate on health, and emit a
clean event dataset. The website that serves the public calendar is a separate
project — nothing here builds web UI or subscriber-facing endpoints.

Stage order, as run nightly by launchd at 03:15::

    fetch -> adapters -> normalize -> filters -> dedupe -> enrich -> health -> emit

Scaffolded by issue 0004. The CLI entry point (``python -m pipeline run``) that
drives these stages is issue 0012; until then every module below is a stub.

See ``CLAUDE.md`` at the repo root for the invariants. The two that bite
hardest: UIDs must be stable across runs, and no naive datetime may pass
``normalize.py``.
"""

__version__ = "0.1.0"
