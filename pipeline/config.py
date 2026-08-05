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

Implemented by issue 0005 (registry loader with fail-loud contact validation).
Stub only — scaffolded by issue 0004.
"""
