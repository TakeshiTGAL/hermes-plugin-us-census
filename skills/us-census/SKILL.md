---
name: us-census
description: US Census population, household income, median age, and housing for a place, with the ACS vintage year, variable id, and margin of error. Before web_search, use tool_search and tool_call. Report only returned figures.
---

Before web_search, use tool_search and then tool_call. The toolset is us-census. Report only figures the tool returned, each with its vintage year, variable id, and margin of error. An ACS vintage is an estimate for that product year. It is a survey estimate even when the vintage year matches the calendar year.

This product uses the Census Bureau Data API but is not endorsed or certified by the Census Bureau.

## What to call

For a place name, call census_place. For two places in one year, call census_compare. For a variable id, a group id, or a keyword such as median age, call census_variables. A keyword opens the first matching groups only. The result says how many groups matched and how many were opened, and it says when that list was cut off. census_place already returns median age as B01002_001E. For one known FIPS geography and up to 10 variable ids, call census_get. For the dataset list, call census_datasets. For geography levels, call census_geographies.

Ordinary requests that should find these tools are "ACS population and margin of error", "census median household income", and "FIPS for a US place".

A street address that starts with a number uses the Census geocoder. A city, county, or state name uses Census TIGERweb. Several matches return candidates and no estimates, and the result names that fact. When that list was cut off, the result says the list was cut off. Zero matches return no invented number, and the result says that no geography matched. A single longer name stays a candidate list. When the returned list is complete and exactly one official name is the asked name, or that name plus one Census class suffix, that place is selected. The asked name is kept whole. Kansas City matches Kansas City city. Grant Town matches Grant Town town. Two or more official names that match in that way stay a candidate list, and no estimate is read. A cut-off list stays a candidate list. Pass one official place name from that list, such as a name ending in city or CDP, to select that place. A bare state name that is also a place name stays a candidate list. A bare state name outside that known list is checked for an exact place or CDP before it is read as the state. If that check fails, the state is not chosen. The postal abbreviation selects the state. The state name followed by State selects the state. Washington, DC is the District of Columbia. A city from the place layer leaves county FIPS empty. An address geocode includes the county of that point. When an address matches several incorporated places, keep the census designated places from that point on the candidate list.

census_compare refuses two different vintage years before any request. Pass one year for both places. Both places use one dataset, acs/acs5 by default. A data call that returns HTTP 204 with an empty body has no row for that geography when the geography is on the Census roster. The result names the vintage and the geography. No county figure is substituted. A county or place FIPS that is absent from the TIGER roster is named as absent from the roster. Do not retry that call as a host error.

census_variables reads keyless metadata. It can run when CENSUS_API_KEY is unset. Estimate tools need the key in this profile's .env. When the key is unset, the result is one sentence and the official key page. An HTML page titled Missing Key is not the estimate. A redirect to the Invalid Key page or the Missing Key page on api.census.gov is refused, and the result is one sentence and the official key page.

A sentinel such as -666666666 is an annotation. Quote the annotation and the note. Do not quote the sentinel as a count. A null cell is missing data. Do not call it zero.

## Schedule

The tools do not write files, so there is no approval card. The tools are on for every surface, including gateways. This plugin keeps no user list. Anyone who can prompt an enabled surface can call an estimate, and that call sends this profile's CENSUS_API_KEY. A guest in a room the gateway admits, when that room has no per-person allowlist, can call an estimate with your key. Turn one surface off with `hermes tools`. Turning telegram off leaves cli on.

Before you run `hermes us-census schedule`, write the target in the reply. The script is `us-census-vintage.py` under HERMES_HOME/scripts. The job name is `us-census-vintage`. Deliver is local. The job is saved for every morning with no model. It fires only when a gateway serves this profile or a fresh ticker heartbeat is present. Creating the job does not start a gateway. It prints one line only when a newer ACS vintage is listed. Empty stdout on a zero exit sends nothing. A failed probe exits non-zero. The reason goes to stderr and stdout stays empty. An unreadable vintage file and an unwritable vintage file do the same. The cron run is then marked failed. The first run stores the current vintage and stays quiet.

The command is `hermes us-census schedule`. Deliver stays local. Another deliver target is refused, and nothing is posted to chat. The macOS `/var` link is accepted, because `mktemp` lives there. Any other symlink in the Hermes home path is refused, and the reply says the vintage script was not written. `hermes us-census status` reads the saved job and the vintage file and does not fetch. `hermes us-census unschedule` removes the job and the script. If the job is already gone, unschedule still removes the script and says that the script was removed. If the vintage file is already stored, unschedule says that file remains until you delete it. If that file was never written, unschedule says that no vintage file is stored. Run unschedule while the plugin is still enabled, before `hermes plugins disable us-census`. After disable, `hermes us-census` is gone and the job stays listed. Remove it with `hermes cron remove` and the job id, then delete `scripts/us-census-vintage.py` in the Hermes home. `hermes plugins remove us-census` deletes the plugin directory. The job, `scripts/us-census-vintage.py`, and the vintage file stay in the Hermes home when they were created.

## When it fails

- missing_key: Add CENSUS_API_KEY to this profile's .env. Request one at the official Census key page. No estimate was read.
- invalid_key: Replace CENSUS_API_KEY in this profile's .env. Request one at the official Census key page. No estimate was read.
- secret_unscoped: Retry inside a Hermes profile. No data request was sent.
- bad_args: The year, dataset, FIPS, variable id, or place text is outside the allowlist. Pass the fields the tool names.
- year_mismatch: Pass one vintage year for both places. No request was sent.
- host_refused: The destination is not a pinned Census host. Nothing was requested.
- redirect: The redirect was not followed. Retry later.
- timeout: The Census host timed out. A timeout is a timeout. One request stops at 20 seconds. Retry later.
- connection_failed: The Census host did not connect. Retry later.
- http_status: HTTP 404 means that year, dataset, or id is unlisted. HTTP 400, 401, and 403 were refused. HTTP 204 with an empty body on a data call means that vintage has no row for a geography on the Census roster, and no county figure is substituted. A county or place FIPS absent from the TIGER roster is named as absent. Other HTTP errors can be retried later.
- unexpected_body: The JSON shape was not the documented table or metadata object. The body was not passed on. A geography service error is a rejected query, and retrying the same query does not fill in a number.
- incomplete: The body was longer than the cap, so it was not parsed.
- vintage_unreadable: The vintage probe failed, so an older year was not labeled as the newest.
- budget: The call budget stopped the request. One tool call stops at 45 seconds and at 12 requests. Narrow the question.
- internal: The request stopped before a figure was read. Retry.
