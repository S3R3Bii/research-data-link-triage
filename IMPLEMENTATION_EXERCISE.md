# Review a DOI without declaring its data lost

A research library maintainer needs an honest queue of links to inspect. A timeout, a rate limit or an empty HTTP 202 response does not establish that a dataset disappeared. This exercise reads saved observations and returns a reason for the next review step. It sends no requests and changes no external records.

Run with Python 3.10 or later, using only the standard library:

```sh
python src/triage_cli.py examples/repeated-not-found.json
python src/triage_cli.py examples/transient-not-found.json
python src/triage_cli.py examples/accepted-incomplete.json
python -m unittest discover -s tests -v
```

The examples are authored inputs, not captured source outcomes. Their identifier is a placeholder that the exercise never resolves.

| Saved evidence | Expected conservative action | What to do |
| --- | --- | --- |
| 404 then 410, at least twenty minutes apart | `review_doi_candidate` | Inspect the DOI routing and source metadata. Do not announce permanent data loss. |
| 404 then 200 | `http_response_observed` | Retain the first failure and later response. Verify page identity separately. |
| 202 then 202 | `unknown` | Record accepted-but-incomplete responses. Inspect the normal page if permitted. |

Every output keeps `data_file_availability=not_tested`. Even an identified landing page does not prove that its files are downloadable, usable or scientifically valid. The twenty-minute interval is an authored study guard, not a validated optimal waiting time.

To use captured study inputs, build a local JSON object with `first` and `second` records from the matching observation files. Keep the same item ID, DOI and role. The CLI rejects mismatched identities, missing timezones, reversed times, invalid status values and claims that this exercise checked dataset files. A repeat before the interval expires stays unknown. HTTP 401/403, rate limits and transport failures also stay unknown under the conservative rule.

An implementation task: add an authored fixture for a redirect to an unsupported host. Preserve the captured terminal status and record the censor reason; verify that the conservative result remains unknown. Then add an authored reference with an unknown label and verify that it does not enter binary precision or recall denominators. Do not fetch new links or revise the study rules from final-group outcomes.

The comparison rule deliberately calls a first non-2xx response or transport failure a `broken_alert`. Its purpose is to expose that interpretation for review. A conservative review candidate has a narrower meaning, so differences in their alert counts are not measured maintainer time savings. No automatic repair or production integration is implemented.
