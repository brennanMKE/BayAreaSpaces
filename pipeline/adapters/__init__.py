"""Source adapters — one module per ``adapter`` value in ``sources.yaml``.

An adapter takes a raw payload from ``fetch`` and returns events in the
canonical schema, before normalization. Adapters are deliberately small and
swappable: ``sources.yaml`` is the only place they are named, so a source that
drifts is repaired by changing one file, or by changing one registry line.

Registered adapters and the issue that implements each:

===============  ============================================================
``ics``          direct iCalendar feed, RRULE expansion clipped to the
                 horizon — issue 0007
``gcal_ics``     Google Calendar public ``basic.ics`` (``calendar_id``), a thin
                 wrapper over ``ics`` — issue 0008
``tribe_rest``   The Events Calendar WP REST API — issue 0019
``jsonld``       schema.org/Event via ``extruct`` (``@graph``, arrays,
                 ``ItemList`` wrappers all appear in the wild here) — issue 0020
``embedded_json``named ``<script type="application/json" id=...>`` blob plus a
                 field map; generalizes ``nextdata`` — issue 0021
``json``         plain JSON document with a bespoke shape, named by ``shape:``
                 in the registry — issue 0022. The module is ``json_doc.py``,
                 not ``json.py``, so nothing in this package shadows the stdlib
                 name it depends on.
``nextdata``     ``__NEXT_DATA__`` blob on Eventbrite organizer pages — issue 0023
``bookwhen_html``server-rendered Bookwhen agenda table — issue 0024
``rss``          RSS/Atom; only an event source when items carry a real start
                 date, which most do not — issue 0025
``llm_html``     last resort: cleaned HTML -> local model -> validated JSON —
                 issue 0028
===============  ============================================================

Only ``llm_html`` involves a model. Treat every new ``llm_html`` entry as a
small failure to find the real feed, and look harder first.

**A wrong adapter is as silent as a wrong URL.** Humanmade's Eventbrite page was
registered as ``jsonld``; Eventbrite has since moved to ``__NEXT_DATA__``, so
that source would have returned empty forever without ever erroring. Adapters
must assert that they actually found the structure they expect.

Scaffolded by issue 0004.
"""
