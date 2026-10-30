# Check the evidence before reporting a research-data link as broken

A failed request can reflect access restrictions, a rate limit or a temporary service problem. It does not establish that a research dataset has disappeared. This study will compare a deliberately naive alert rule with an explicit triage rule, using fixed public article-to-dataset metadata, repeat observations and separately inspected reference pages.

**State: development first observations and browser references checked; timed repeat and separate final group pending.** Eight real development links returned empty HTTP 202 responses, while their normal browser pages visibly matched the dataset DOIs. All three authored controls showed DOI Not Found. These are observations of different access paths at different times, not a test of dataset-file availability.

The practical output is an [offline implementation exercise](IMPLEMENTATION_EXERCISE.md): read captured observations, retain uncertain states, and suggest DOI review only after two spaced not-found responses. Its [fixed rules](src/rules.py) are authored comparators, with no model fitting or optimized waiting interval. No false-alert reduction, actual workload saving, repair or production deployment is established.

The [protocol](PROTOCOL.md) fixes the cohort, request bounds, reference labels, repeat interval, rules, separate-link evaluation and release checks. The exact qualified cohort contains sixteen Karger Figshare datasets; it does not establish reliability across repositories. References are agent-inspected pages, without independent human validation. No alerts are sent to maintainers.
