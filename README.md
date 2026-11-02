# US Census

Ask for a US place and get official ACS population, median household income, median age, and housing figures. Each figure carries its vintage year, variable id, and margin of error. The vintage is the ACS product year. It stays an estimate when that year is also the calendar year.

This product uses the Census Bureau Data API but is not endorsed or certified by the Census Bureau.

The tools are `census_datasets`, `census_geographies`, `census_variables`, `census_place`, `census_get`, and `census_compare`.

## Key

Estimate queries need `CENSUS_API_KEY` in this profile's `.env`. The key is free and you request it yourself. This plugin does not create a Census account and does not issue a key. Request one at https://www.census.gov/data/developers/guidance/api-user-guide.API_Key.html

The plugin reads the key on each data call through `agent.secret_scope.get_secret`. The profile `.env` wins when it sets the key. If it does not, and this process is not serving a routed profile, `get_secret` reads the process environment. A routed profile does not fall through to the process environment. Metadata, the geocoder, and TIGERweb are requested without the key. `census_variables` is one of those keyless calls.

If the key is unset, the data endpoint is not called. The tool returns one fixed sentence and the key page above. If the data server still answers HTTP 200 with an HTML page titled Missing Key, that HTML is dropped and the same kind of sentence is returned. If a data call is sent and the server redirects to its Invalid Key page or its Missing Key page on `api.census.gov`, the redirect is refused and that page is not fetched. The tool returns one fixed sentence and the key page above. A redirect to any other host stays a redirect.

## Hosts

HTTPS only, to `api.census.gov`, `geocoding.geo.census.gov`, and `tigerweb.geo.census.gov`. A caller cannot pass a URL. Redirects are refused. `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `http_proxy`, `https_proxy`, `NO_PROXY`, `no_proxy`, `all_proxy`, and `ftp_proxy` are not used. Each request stops at 20 seconds. One tool call stops at 45 seconds and at 12 requests. A response over the size cap is a failure, and the cut-off body is not parsed.

A street address that starts with a number uses the Census geocoder (`geographies/onelineaddress`). A city, county, or state name uses TIGERweb. Several matches return candidates and no estimates, and the result says that several geographies matched. When that list was cut off, the result says the list was cut off. Zero matches do not invent a number, and the result says that no geography matched. A single longer name stays a candidate list. When the returned list is complete and exactly one official name is the asked name, or that name plus one Census class suffix, that place is selected. The asked name is kept whole. Kansas City matches Kansas City city. Grant Town matches Grant Town town. Carson City matches Carson City. Two or more official names that match in that way stay a candidate list, and no estimate is read. A cut-off list stays a candidate list. A name ending in city or CDP selects that place when it matches once. A bare state name that is also a place name stays a candidate list. A bare state name outside that known list is checked for an exact place or CDP before it is read as the state. If that check fails, the state is not chosen. The postal abbreviation selects the state. The state name followed by State selects the state. The matched geography name is the one on the sheet. State FIPS stays on the sheet. A county match includes county FIPS. A city from the place layer leaves county FIPS empty. An address geocode includes the county of that point. When an address matches several incorporated places, census designated places from that point stay on the candidate list. Census headquarters geocodes to Suitland CDP in Maryland, and the sheet says so.

`census_compare` keeps one vintage year. Different years are refused before any request. The default dataset is `acs/acs5`. `acs/acs1` is the other allowed dataset for estimates. A 1-year table with no row for that geography does not borrow a county figure. A data call that returns HTTP 204 with an empty body is that empty geography for ACS 1-year and for ACS 5-year when the geography is on the Census roster. The result names the vintage and the geography. No county figure is substituted. A county or place FIPS that is absent from the TIGER roster is named as absent from the roster.

Negative special codes such as `-666666666` are annotations. They are not counts. A null cell is not zero.

## Schedule

There is no approval card. The tools only read.

Before you run `hermes us-census schedule`, write the target in the reply. The script is `us-census-vintage.py` under `HERMES_HOME/scripts`. The job name is `us-census-vintage`. Deliver is local. The job is saved for every morning with `--no-agent`. It fires only when a gateway serves this profile or a fresh ticker heartbeat is present. `hermes cron list` reports that the scheduler is not ready when neither is present. Creating the job does not start a gateway. It prints one line only when a newer ACS vintage is listed. Empty stdout on a zero exit sends nothing. A failed probe exits non-zero. The reason goes to stderr and stdout stays empty. An unreadable vintage file and an unwritable vintage file do the same. The cron run is then marked failed. The first run stores the listed vintage and stays quiet.

```bash
hermes us-census schedule
hermes us-census status
hermes us-census unschedule
```

`hermes us-census schedule --deliver local` is the only deliver target. Any other target is refused before the job is created, so this plugin does not post to chat. Running schedule again updates the one job of that name. If more than one job already has that name, nothing is written and nothing is changed.

`status` does not fetch. It prints the job and the stored vintage. `unschedule` removes the job and the script. If the job is already gone, `unschedule` still removes the script and says that the script was removed. If `plugin-data/us-census/vintage.json` is already stored, unschedule says that file remains until you delete it. If that file was never written, unschedule says that no vintage file is stored. Run `hermes us-census unschedule` while the plugin is still enabled, before `hermes plugins disable us-census`. After disable, `hermes us-census` is gone, and the job stays listed. Remove that job with `hermes cron remove` and the job id. `hermes cron remove` leaves `scripts/us-census-vintage.py` in the Hermes home, so delete that file too. The vintage file holds the vintage baseline. It does not hold the key or the last place. `hermes plugins remove us-census` deletes the plugin directory. The cron job `us-census-vintage`, `scripts/us-census-vintage.py`, and `plugin-data/us-census/vintage.json` stay in the Hermes home when they were created. Remove the job with `hermes cron remove`, then delete the script and the vintage file.

When `HERMES_HOME` is unset, the directory is the Hermes default, `HOME/.hermes` on this platform, from `hermes_constants.get_hermes_home()`. The macOS `/var` link is accepted, because `mktemp` lives there. Any other symlink in the Hermes home path is refused, and the reply says the vintage script was not written.

The tools are on for every surface, including gateways. This plugin keeps no user list. Anyone who can prompt an enabled surface can call an estimate, and that call sends this profile's CENSUS_API_KEY. A guest in a room the gateway admits, when that room has no per-person allowlist, can call an estimate with your key. Turn one surface off with `hermes tools`. Turning telegram off leaves cli on. There is no daily cap and no slash command. The agent can call the tools itself.

`requires_hermes` is `>=0.21.4`. `python_dependencies` is empty. The license is MIT.

## Thirty second check

Run these from this repository with `hermes` on `PATH`. The check calls one keyless variable lookup. It does not need `CENSUS_API_KEY`.

Empty `HERMES_HOME`:

```bash
export HERMES_HOME="$(mktemp -d)"
hermes plugins install --no-enable "file://$PWD"
hermes plugins enable us-census
python3 - <<'PY'
import importlib.util
import os
import sys
from pathlib import Path

root = Path(os.environ["HERMES_HOME"])
plugin = root / "plugins" / "us-census"
spec = importlib.util.spec_from_file_location(
    "us_census_plugin",
    str(plugin / "__init__.py"),
    submodule_search_locations=[str(plugin)],
)
module = importlib.util.module_from_spec(spec)
sys.modules["us_census_plugin"] = module
spec.loader.exec_module(module)
print(module.tools.HANDLERS["census_variables"]({
    "code": "B01003_001E",
    "year": 2023,
    "dataset": "acs/acs5",
}))
PY
```

Unset `HERMES_HOME`, with `HOME` pointed at a new directory:

```bash
export HOME="$(mktemp -d)/user"
mkdir -p "$HOME"
unset HERMES_HOME
hermes plugins install --no-enable "file://$PWD"
hermes plugins enable us-census
python3 - <<'PY'
import importlib.util
import os
import sys
from pathlib import Path

root = Path.home() / ".hermes"
plugin = root / "plugins" / "us-census"
spec = importlib.util.spec_from_file_location(
    "us_census_plugin",
    str(plugin / "__init__.py"),
    submodule_search_locations=[str(plugin)],
)
module = importlib.util.module_from_spec(spec)
sys.modules["us_census_plugin"] = module
spec.loader.exec_module(module)
print(module.tools.HANDLERS["census_variables"]({
    "code": "B01003_001E",
    "year": 2023,
    "dataset": "acs/acs5",
}))
PY
```

## What this plugin does not do

It does not rank every place in a state. It does not download the full variables file. It does not fetch Population Estimates Program tables. It does not save the last place. It does not send the morning note to chat. It does not open a payment page. The install copies the tests directory and pytest.ini with the plugin, and both stay unloaded. assets/catalog-banner.png is copied with the install and stays unloaded. After hermes plugins install, the plugin directory including .git is 644 kilobytes by du -sk.
