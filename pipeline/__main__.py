"""``python -m pipeline`` — the entry point launchd runs at 03:15.

Deliberately three lines. Everything the run does lives in
:mod:`pipeline.cli`, which returns an exit code rather than calling
:func:`sys.exit`, so the whole nightly job stays importable and testable
without a subprocess.

Exit codes are the contract with launchd's log: see ``EXIT_OK``,
``EXIT_HEALTH_BLOCKED``, ``EXIT_CONFIG`` and ``EXIT_ERROR`` in
:mod:`pipeline.cli`.

Implemented by issue 0012.
"""

from __future__ import annotations

import sys

from pipeline.cli import main

if __name__ == "__main__":
    sys.exit(main())
