"""Scaffold smoke tests: the package imports and the pinned deps are installed.

Cheap, but they catch the two things that make a nightly run fail at 03:15 with
nothing useful in the log: a module that does not import, and a dependency that
was named in the issue but never actually added to ``pyproject.toml``.
"""

import importlib

import pytest

PIPELINE_MODULES = [
    "pipeline",
    "pipeline.config",
    "pipeline.fetch",
    "pipeline.adapters",
    "pipeline.normalize",
    "pipeline.dedupe",
    "pipeline.enrich",
    "pipeline.emit_ics",
    "pipeline.emit_rss",
    "pipeline.health",
    "pipeline.verify",
]

# The dependency list from issue 0004, by import name.
REQUIRED_DEPENDENCIES = [
    "httpx",
    "icalendar",
    "recurring_ical_events",
    "extruct",
    "dateutil",
    "rapidfuzz",
    "feedgen",
    "pydantic",
    "yaml",
]


@pytest.mark.parametrize("name", PIPELINE_MODULES)
def test_pipeline_module_imports(name):
    module = importlib.import_module(name)
    assert module.__doc__, f"{name} needs a module docstring stating its job"


@pytest.mark.parametrize("name", REQUIRED_DEPENDENCIES)
def test_dependency_is_installed(name):
    importlib.import_module(name)


def test_http2_support_is_available():
    """httpx is pinned with the [http2] extra; the fetch layer relies on it."""
    importlib.import_module("h2")
