"""Pinned Census hosts and body caps. No other host is requested."""

from __future__ import annotations

API_HOST = "api.census.gov"
GEO_HOST = "geocoding.geo.census.gov"
TIGER_HOST = "tigerweb.geo.census.gov"

ALLOWED_HOSTS = frozenset({API_HOST, GEO_HOST, TIGER_HOST})

CAP_GEO = 1_000_000
CAP_GROUP = 1_000_000
CAP_GROUPS = 1_000_000
CAP_GEOGRAPHY = 1_000_000
CAP_CATALOG = 8_000_000
CAP_DATA = 1_000_000
CAP_VARIABLE = 1_000_000
CAP_DESCRIPTOR = 1_000_000

USER_AGENT = "hermes-plugin-us-census/0.1.0"

REQUEST_TIMEOUT = 20.0
TOOL_BUDGET = 45.0
MAX_CALLS = 12

PLACES_LAYER = "/arcgis/rest/services/TIGERweb/Places_CouSub_ConCity_SubMCD/MapServer/4/query"
CDP_LAYER = "/arcgis/rest/services/TIGERweb/Places_CouSub_ConCity_SubMCD/MapServer/5/query"
COUNTY_LAYER = "/arcgis/rest/services/TIGERweb/State_County/MapServer/1/query"
# A layer returns HTTP 200 with an ArcGIS error when outFields names a column it lacks.
# The place layers reject COUNTY. A city match leaves county FIPS empty.
PLACE_FIELDS = "NAME,STATE,PLACE,GEOID"
COUNTY_FIELDS = "NAME,STATE,COUNTY,GEOID"
GEOCODER_PATH = "/geocoder/geographies/onelineaddress"

DATASETS = ("acs/acs5", "acs/acs1")
YEAR_MIN = 2005
CANDIDATE_CAP = 20
GROUP_OPEN_CAP = 3
DATASET_PAGE = 40
VARIABLE_CAP = 10
NAME_MAX = 180
