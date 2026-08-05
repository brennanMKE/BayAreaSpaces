"""The only stage that calls a model.

Talks to LM Studio's OpenAI-compatible API at ``http://localhost:1234/v1`` with
a strict JSON schema, for exactly two jobs:

1. **Unstructured HTML -> JSON extraction**, where no stable selectors exist.
   Used by the ``llm_html`` adapter only; exactly one registered source needs it.
2. **Categorization and summarization** — interest tags plus a one-line RSS
   summary, seeded from ``references/categories.json``.

Rules that are not negotiable:

- **Never emit unvalidated model output.** Every response is parsed through a
  Pydantic model before it can reach ``dedupe``, ``emit`` or the database.
- **The model returns source-verbatim date strings**, never computed ISO
  timestamps. ``dateutil`` plus the known source timezone does the math.
- Never let model output into a UID.
- Use a small instruct model (3B-8B); escalate only on quarantined pages.
- **If LM Studio is not answering, skip this stage and publish Tier A/B only**
  rather than failing the run. Structured data must never be held hostage to the
  model being up.

Implemented by issue 0027 (LM Studio client with Pydantic validation) and issue
0029 (categorization and one-line summaries). Stub only — scaffolded by issue
0004.
"""
