# Bay Area Spaces

Issue tracker for the Bay Area makerspace event aggregator — the nightly Python job that reads `sources.yaml`, fetches and parses event feeds from Bay Area makerspaces, normalizes and dedupes them, and publishes a clean event dataset. The website that turns that dataset into a public calendar and RSS feed is a **separate project**; issues about web UI, hosting, or subscriber-facing endpoints do not belong here.

This file is the local guide for managing issues in this project. The companion Mac app (Issues.app) watches the `issues/` folder and renders the current state. Markdown files (and `project.json`) are the source of truth — there is no generated artifact or index to keep in sync.

**Read `CLAUDE.md` at the repo root before working any issue.** It carries the architecture, the language decision, the invariants, and the health-gate design. It is binding for code conventions and it wins over this file on anything code-related.

## Status values

| File value | Display name | Meaning |
|---|---|---|
| `open` | Open | Filed but not yet started |
| `in-progress` | In Progress | Actively being worked on |
| `resolved` | Resolved | Work is done; awaiting user confirmation |
| `closed` | Closed | User has confirmed the fix |
| `wontfix` | Won't Fix | Acknowledged but won't be addressed |

Use the file value (lowercase, hyphenated) in the metadata table.

## Critical rule: never close without explicit confirmation

An issue must never be marked `resolved`, `closed`, or `wontfix` based on inference — only when the user says so in plain language. Do not infer resolution from a code change, a commit message, or "thanks, that looks better". A subagent that finishes a fix may set `resolved`; only the user sets `closed`.

## Git tracking

`issues/` **is tracked** in this repo. Lifecycle events produce commits:

| Event | Commit message |
|---|---|
| File a new issue | `#NNNN <issue title>` |
| Resolve — code commit | `#NNNN <verb> <title>` |
| Resolve — resolution commit | `#NNNN Resolve: <title>` |
| Bail with notes | `#NNNN Notes: <brief>` |
| User-confirmed close | `#NNNN Close` |
| Won't fix | `#NNNN Won't fix` |

Setting status to `in-progress` is a working-copy edit only — no commit.

Commit directly to `main`; this is a solo repo and feature branches are not used.

## Build / verify command for this project

**No pipeline code exists yet** — issue 0004 creates the scaffold. Once it does:

```bash
uv run pytest                 # unit tests
uv run python -m pipeline run --dry-run   # end-to-end without publishing
python3 -c "import yaml; yaml.safe_load(open('sources.yaml'))"   # registry must always parse
```

Until the test suite exists, "verification" for a registry or docs change is that `sources.yaml` still parses and the claim being recorded was actually fetched and observed. **Never mark an adapter issue resolved on the strength of a green build** — CLAUDE.md's "HTTP 200 is not success" rule applies to our own code too. An adapter is verified when it returns real, dated events from the live source and the count matches what the research file recorded.

## Module conventions for this project

- `registry` — `sources.yaml` and its schema
- `fetch` — HTTP layer, rate limiting, robots.txt, `raw/` archiving
- `adapters` — one per source type (`ics`, `gcal_ics`, `tribe_rest`, `jsonld`, `nextdata`, `embedded_json`, `json`, `rss`, `bookwhen_html`, `llm_html`)
- `normalize` — timezone handling, UID assignment, filters
- `dedupe` — cross-source and cross-space collision handling
- `enrich` — the two LLM jobs (extraction, categorization)
- `emit` — ICS and RSS output
- `health` — publish gates, `health.json`, alerting
- `publish` — Postgres on EC2
- `schedule` — launchd
- `research` — source discovery and per-space notes in `spaces/`
- `outreach` — contacting spaces
- `docs` — README, CLAUDE.md, this file

## Platform

This project runs on a Mac mini (M4 Pro) under launchd, with LM Studio serving local models. Use `macOS` for runtime work and `All` for registry, docs, research and outreach issues.
