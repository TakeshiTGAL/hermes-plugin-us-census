"""Tool operations. Read tools do not write. A data call waits for one geography and a key."""

from __future__ import annotations

import datetime
import time

from .acs import (
    BUNDLE,
    GROUP_OPEN_CAP,
    bad_dataset,
    bad_year,
    bundle_columns,
    catalog_url,
    data_url,
    dataset_ok,
    geography_clauses,
    geography_url,
    group_url,
    groups_url,
    keyword_hits,
    rank_groups,
    parse_catalog,
    parse_geographies,
    parse_group_file,
    parse_groups_index,
    parse_table,
    parse_variable,
    parse_year,
    product_label,
    sheet,
    siblings,
    user_variables,
    variable_url,
    variable_rank,
    variables_in_group,
)
from .errors import KEY_NEXT, KEY_UNSET, fail
from .geography import fips_roster, resolve_place
from .hosts import (
    CAP_CATALOG,
    CAP_DATA,
    CAP_DESCRIPTOR,
    CAP_GEO,
    CAP_GEOGRAPHY,
    CAP_GROUP,
    CAP_GROUPS,
    CAP_VARIABLE,
    DATASET_PAGE,
    MAX_CALLS,
    REQUEST_TIMEOUT,
    TOOL_BUDGET,
    VARIABLE_CAP,
)
from .http_client import HttpResult, as_failure, fetch as live_fetch
from .vintage import calendar_year, discover

_CAPS = {
    "geo": CAP_GEO,
    "group": CAP_GROUP,
    "groups": CAP_GROUPS,
    "geography": CAP_GEOGRAPHY,
    "catalog": CAP_CATALOG,
    "data": CAP_DATA,
    "variable": CAP_VARIABLE,
    "descriptor": CAP_DESCRIPTOR,
}

_PLACE_KEYS = {"place", "year", "dataset"}
_COMPARE_KEYS = {"place_a", "place_b", "year", "year_a", "year_b", "dataset"}
_GET_KEYS = {"year", "dataset", "variables", "state_fips", "county_fips", "place_fips"}
_VARIABLE_KEYS = {"query", "code", "year", "dataset", "limit"}
_DATASET_KEYS = {"query", "limit"}
_GEO_KEYS = {"year", "dataset"}


def read_api_key():
    """Read CENSUS_API_KEY through agent.secret_scope.get_secret.

    The profile scope supplies the value when it has one. When no scope is
    active, or the scope misses and the profile is not routed, get_secret
    reads the process environment. A routed profile does not fall through.
    """
    try:
        from agent.secret_scope import UnscopedSecretError, get_secret
    except ImportError:
        return None, "missing"
    try:
        value = get_secret("CENSUS_API_KEY", default=None)
    except UnscopedSecretError:
        return None, "unscoped"
    except Exception:
        return None, "unscoped"
    if value is None:
        return None, "missing"
    text = str(value).strip()
    if not text:
        return None, "missing"
    if any(ord(char) < 33 or ord(char) > 126 for char in text):
        return None, "bad"
    return text, None


def _default_transport(url, cap="data", timeout=REQUEST_TIMEOUT):
    return live_fetch(url, timeout=timeout, max_bytes=_CAPS[cap])


class Census:
    def __init__(self, transport=None, key_reader=None, clock=None):
        self.transport = transport or _default_transport
        self.key_reader = key_reader or read_api_key
        self.clock = clock
        self._known = {}
        self.calls = 0
        self.started = 0.0

    def _begin(self):
        self.calls = 0
        self.started = time.monotonic()

    def _now_year(self) -> int:
        if self.clock is None:
            return calendar_year()
        return calendar_year(self.clock())

    def _fetch(self, url, cap="data"):
        self.calls += 1
        if self.calls > MAX_CALLS:
            return HttpResult("budget")
        remain = TOOL_BUDGET - (time.monotonic() - self.started)
        if remain <= 0.5:
            return HttpResult("budget")
        timeout = min(REQUEST_TIMEOUT, remain)
        return self.transport(url, cap=cap, timeout=timeout)

    def _failure(self, result: HttpResult):
        if result.kind == "budget":
            return fail(
                "budget",
                "The request stopped at the call budget, so no estimate was read.",
                "Narrow the question and retry. Nothing partial was saved.",
            )
        return as_failure(result)

    def _prepare(self, args, allowed):
        self._begin()
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return None, fail("bad_args", "Arguments must be an object.", "Pass the fields named in the tool schema.")
        if any(key not in allowed for key in args):
            return None, fail("bad_args", "An argument is not in the tool schema.", "Use only the fields named in the tool schema.")
        return args, None

    def _dataset(self, args):
        dataset = dataset_ok(args.get("dataset"))
        if dataset is None:
            return None, bad_dataset()
        return dataset, None

    def _explicit_year(self, args):
        if "year" not in args or args.get("year") is None:
            return None, None
        year = parse_year(args.get("year"), self._now_year())
        if year is None:
            return None, bad_year()
        return year, None

    def _vintage(self, dataset, explicit):
        if explicit is not None:
            return explicit, None
        if dataset in self._known:
            return self._known[dataset], None
        listed, failed = discover(self._fetch, dataset, self._now_year())
        if failed:
            missed = fail(
                "vintage_unreadable",
                "The vintage probe failed, so an older year was not labeled as the newest.",
                "Retry later. No estimate was read.",
            )
            missed["estimates_read"] = False
            return None, missed
        if listed is None:
            missed = fail(
                "vintage_unreadable",
                "No ACS vintage in the probe window was listed, so no year was chosen.",
                "Pass a vintage year. No estimate was read.",
            )
            missed["estimates_read"] = False
            return None, missed
        self._known[dataset] = listed
        return listed, None

    def datasets(self, args):
        args, problem = self._prepare(args, _DATASET_KEYS)
        if problem:
            return problem
        limit = _limit(args.get("limit"), DATASET_PAGE, DATASET_PAGE)
        if limit is None:
            return fail("bad_args", "Limit must be an integer from 1 through 40.", "Pass a limit from 1 through 40.")
        query = args.get("query", "acs")
        if not isinstance(query, str) or len(query) > 80:
            return fail("bad_args", "Query must be a short string.", "Pass a dataset keyword such as acs, or an empty string for the first page of the catalog.")
        result = self._fetch(catalog_url(), "catalog")
        failure = self._failure(result)
        if failure:
            failure["estimates_read"] = False
            failure["complete"] = False
            return failure
        parsed = parse_catalog(result.body, query, limit)
        if parsed is None:
            return fail(
                "unexpected_body",
                "The dataset catalog did not have a dataset array, so no list was returned.",
                "Retry later. An empty catalog was not claimed.",
            )
        if parsed.get("truncated"):
            parsed["sentence"] = (
                "The dataset list was cut off. This result has "
                + str(parsed["returned"]) + " of " + str(parsed["total"]) + " rows."
            )
        return parsed

    def geographies(self, args):
        args, problem = self._prepare(args, _GEO_KEYS)
        if problem:
            return problem
        dataset, problem = self._dataset(args)
        if problem:
            return problem
        explicit, problem = self._explicit_year(args)
        if problem:
            return problem
        year, problem = self._vintage(dataset, explicit)
        if problem:
            return problem
        result = self._fetch(geography_url(dataset, year), "geography")
        failure = self._failure(result)
        if failure:
            failure["estimates_read"] = False
            failure["complete"] = False
            return failure
        parsed = parse_geographies(result.body, year, dataset)
        if parsed is None:
            return fail(
                "unexpected_body",
                "The geography list did not have a fips array, so no levels were returned.",
                "Retry later. An empty level list was not claimed.",
            )
        return parsed

    def variables(self, args):
        args, problem = self._prepare(args, _VARIABLE_KEYS)
        if problem:
            return problem
        dataset, problem = self._dataset(args)
        if problem:
            return problem
        explicit, problem = self._explicit_year(args)
        if problem:
            return problem
        code = args.get("code")
        query = args.get("query")
        if code is None and query is None:
            return fail("bad_args", "Pass a variable code or a search query.", "Use code for one variable id, or query for a keyword.")
        if code is not None and not isinstance(code, str):
            return fail("bad_args", "Code must be a variable id string.", "Pass a variable id such as the total-population estimate id.")
        if query is not None and (not isinstance(query, str) or len(query) > 80):
            return fail("bad_args", "Query must be a short string.", "Pass a keyword, a group id, or a variable id.")
        limit = _limit(args.get("limit"), 20, 40)
        if limit is None:
            return fail("bad_args", "Limit must be an integer from 1 through 40.", "Pass a limit from 1 through 40.")
        year, problem = self._vintage(dataset, explicit)
        if problem:
            return problem
        if code:
            return self._one_variable(dataset, year, code)
        from .acs import GROUP_ID, VAR_ID
        if VAR_ID.match(query):
            return self._one_variable(dataset, year, query)
        if GROUP_ID.match(query):
            return self._one_group(dataset, year, query, limit)
        return self._keyword(dataset, year, query, limit)

    def _one_variable(self, dataset, year, code):
        from .acs import VAR_ID
        if not VAR_ID.match(code):
            return fail("bad_args", "Code must be an estimate or margin variable id.", "Pass an id that ends in E or M, such as the total-population estimate.")
        result = self._fetch(variable_url(dataset, year, code), "variable")
        failure = self._failure(result)
        if failure:
            failure["estimates_read"] = False
            return failure
        parsed = parse_variable(result.body, year, dataset)
        if parsed is None:
            return fail(
                "unexpected_body",
                "The variable metadata did not have a name, so no variable was returned.",
                "Retry later. A label was not invented.",
            )
        return parsed

    def _one_group(self, dataset, year, group, limit):
        result = self._fetch(group_url(dataset, year, group), "group")
        failure = self._failure(result)
        if failure:
            failure["estimates_read"] = False
            failure["complete"] = False
            return failure
        rows = parse_group_file(result.body)
        if rows is None:
            return fail(
                "unexpected_body",
                "The group file did not have a variables object, so no variables were returned.",
                "Retry later. An empty variable list was not claimed.",
            )
        page = rows[:limit]
        return {
            "ok": True,
            "estimates_read": False,
            "complete": len(page) == len(rows),
            "vintage_year": year,
            "dataset": dataset,
            "reads_as_calendar_year": False,
            "groups_matched": 1,
            "groups_opened": 1,
            "search_capped": len(rows) > len(page),
            "total": len(rows),
            "variables": page,
        }

    def _keyword(self, dataset, year, query, limit):
        result = self._fetch(groups_url(dataset, year), "groups")
        failure = self._failure(result)
        if failure:
            failure["estimates_read"] = False
            failure["complete"] = False
            return failure
        groups = parse_groups_index(result.body)
        if groups is None:
            return fail(
                "unexpected_body",
                "The group index did not have a groups array, so no search result was returned.",
                "Retry later. An empty variable list was not claimed.",
            )
        matched = rank_groups(keyword_hits(groups, query), query)
        opened = matched[:GROUP_OPEN_CAP]
        found = []
        opened_count = 0
        for group in opened:
            loaded = self._fetch(group_url(dataset, year, group["name"]), "group")
            failure = self._failure(loaded)
            if failure:
                failure["estimates_read"] = False
                failure["complete"] = False
                failure["groups_matched"] = len(matched)
                failure["groups_opened"] = opened_count
                return failure
            opened_count += 1
            rows = parse_group_file(loaded.body)
            if rows is None:
                return fail(
                    "unexpected_body",
                    "A group file did not have a variables object, so the search was not finished.",
                    "Retry later. An empty variable list was not claimed.",
                )
            found.extend(variables_in_group(rows, query))
        found.sort(key=variable_rank)
        page = found[:limit]
        capped = len(matched) > len(opened) or len(found) > len(page)
        if len(matched) == 0:
            sentence = (
                "The search read group names and descriptions. "
                "No group name or description matched, so variable text was not read."
            )
            complete = False
        else:
            sentence = (
                "The search read group names and descriptions, then opened "
                + str(len(opened)) + " of " + str(len(matched)) + " matching groups."
            )
            complete = not capped
            if len(found) > len(page):
                sentence += " The variable list was cut off."
        return {
            "ok": True,
            "estimates_read": False,
            "complete": complete,
            "vintage_year": year,
            "dataset": dataset,
            "reads_as_calendar_year": False,
            "groups_matched": len(matched),
            "groups_opened": len(opened),
            "search_capped": capped,
            "total": len(found),
            "variables": page,
            "sentence": sentence,
        }

    def place(self, args):
        args, problem = self._prepare(args, _PLACE_KEYS)
        if problem:
            return problem
        if "place" not in args:
            return fail("bad_args", "Place is required.", "Pass a city, county, state, or street address.")
        dataset, problem = self._dataset(args)
        if problem:
            return problem
        explicit, problem = self._explicit_year(args)
        if problem:
            return problem
        resolved = resolve_place(args.get("place"), self._fetch)
        if resolved.kind == "error":
            return resolved.payload
        if resolved.kind != "one":
            return _unresolved(resolved.payload)
        year, problem = self._vintage(dataset, explicit)
        if problem:
            problem["geography"] = _geo(resolved.payload)
            problem["estimates_read"] = False
            return problem
        variables = [(variable_id, label) for variable_id, label in BUNDLE]
        return self._estimates(dataset, year, resolved.payload, variables, bundle_columns())

    def get(self, args):
        args, problem = self._prepare(args, _GET_KEYS)
        if problem:
            return problem
        dataset, problem = self._dataset(args)
        if problem:
            return problem
        explicit, problem = self._explicit_year(args)
        if problem:
            return problem
        clauses = geography_clauses(args)
        if clauses is None:
            return fail(
                "bad_args",
                "Geography needs a 2-digit state FIPS, and a 3-digit county or 5-digit place when you ask for one. Wildcards are refused.",
                "Pass state_fips and, for a county or place, the matching FIPS. Do not pass a star.",
            )
        variables = user_variables(args.get("variables"))
        if variables is None:
            return fail(
                "bad_args",
                "Variables must be 1 to " + str(VARIABLE_CAP) + " estimate or margin ids.",
                "Pass estimate ids such as the total-population id. The margin id is added for each estimate.",
            )
        year, problem = self._vintage(dataset, explicit)
        if problem:
            return problem
        geography = {
            "name": None,
            "summary": clauses["summary"],
            "state_fips": clauses["state_fips"],
            "state_abbr": None,
            "county_fips": clauses["county_fips"],
            "place_fips": clauses["place_fips"],
            "geoid": None,
            "for": clauses["for"],
            "in": clauses["in"],
        }
        columns = ["NAME"]
        for variable_id in variables:
            for column in siblings(variable_id):
                if column not in columns:
                    columns.append(column)
        labeled = [(variable_id, variable_id) for variable_id in variables]
        return self._estimates(dataset, year, geography, labeled, columns)

    def compare(self, args):
        args, problem = self._prepare(args, _COMPARE_KEYS)
        if problem:
            return problem
        mismatch = _year_mismatch(args)
        if mismatch is not None:
            return mismatch
        if not isinstance(args.get("place_a"), str) or not isinstance(args.get("place_b"), str):
            return fail("bad_args", "Compare needs two place names.", "Pass place_a and place_b as text.")
        dataset, problem = self._dataset(args)
        if problem:
            return problem
        if "year_a" in args or "year_b" in args:
            explicit = parse_year(_plain_int(args.get("year_a", args.get("year"))), self._now_year())
            if explicit is None:
                return bad_year()
        else:
            explicit, problem = self._explicit_year(args)
            if problem:
                return problem
        left = resolve_place(args.get("place_a"), self._fetch)
        if left.kind == "error":
            return left.payload
        right = resolve_place(args.get("place_b"), self._fetch)
        if right.kind == "error":
            return right.payload
        if left.kind != "one" or right.kind != "one":
            cut = bool(left.kind != "one" and left.payload.get("truncated")) or bool(
                right.kind != "one" and right.payload.get("truncated")
            )
            sentence = "No single geography was chosen, so no estimate was read."
            if cut:
                sentence = "No single geography was chosen. A candidate list was cut off, so no estimate was read."
            return {
                "ok": True,
                "resolved": False,
                "estimates_read": False,
                "sentence": sentence,
                "place_a": _unresolved(left.payload) if left.kind != "one" else {"resolved": True, "geography": _geo(left.payload), "estimates_read": False},
                "place_b": _unresolved(right.payload) if right.kind != "one" else {"resolved": True, "geography": _geo(right.payload), "estimates_read": False},
            }
        year, problem = self._vintage(dataset, explicit)
        if problem:
            problem["estimates_read"] = False
            return problem
        variables = [(variable_id, label) for variable_id, label in BUNDLE]
        columns = bundle_columns()
        first = self._estimates(dataset, year, left.payload, variables, columns)
        if not first.get("ok"):
            first["estimates_read"] = False
            return first
        second = self._estimates(dataset, year, right.payload, variables, columns)
        if not second.get("ok"):
            return fail(
                second.get("error_type", "unexpected_body"),
                "The second geography was not read, so the comparison was not returned.",
                second.get("next_step") or "A one-sided comparison was not returned.",
            )
        return {
            "ok": True,
            "resolved": True,
            "estimates_read": bool(first.get("estimates_read")) and bool(second.get("estimates_read")),
            "vintage_year": year,
            "dataset": dataset,
            "reads_as_calendar_year": False,
            "same_year": True,
            "place_a": first,
            "place_b": second,
        }

    def _estimates(self, dataset, year, geography, variables, columns):
        key, problem = self.key_reader()
        if problem == "unscoped":
            return fail(
                "secret_unscoped",
                "The key scope refused the request, so no estimate was read.",
                "Retry inside a Hermes profile. No data request was sent.",
            )
        if problem == "bad":
            return fail(
                "bad_args",
                "CENSUS_API_KEY is not a single visible line, so no estimate was read.",
                "Replace the key in this profile's .env. The value was not sent.",
            )
        if problem == "missing" or not key:
            missed = fail("missing_key", KEY_UNSET, KEY_NEXT)
            missed["geography"] = _geo(geography)
            missed["vintage_year"] = year
            missed["dataset"] = dataset
            missed["reads_as_calendar_year"] = False
            missed["estimates_read"] = False
            missed["variables"] = [{"variable_id": variable_id, "label": label} for variable_id, label in variables]
            return missed
        if "*" in geography.get("for", "") or "*" in (geography.get("in") or ""):
            return fail("bad_args", "A wildcard geography is refused.", "Pass one state, county, or place.")
        url = data_url(dataset, year, columns, geography["for"], geography.get("in"), key)
        result = self._fetch(url, "data")
        if result.kind in (
            "missing_key_html",
            "missing_key_redirect",
            "invalid_key_html",
            "invalid_key_redirect",
        ):
            missed = self._failure(result) or fail(
                "invalid_key",
                "The data server rejected the key, so no estimate was read.",
                "Replace CENSUS_API_KEY in this profile's .env. Request one at the official Census key page.",
            )
            missed["estimates_read"] = False
            missed["geography"] = _geo(geography)
            missed["vintage_year"] = year
            missed["dataset"] = dataset
            missed["reads_as_calendar_year"] = False
            return missed
        if result.kind == "empty_table":
            absent = _reject_absent_fips(self, dataset, year, geography)
            if absent is not None:
                return absent
            return _absent_row(dataset, year, geography, http_204=True)
        failure = self._failure(result)
        if failure:
            failure["estimates_read"] = False
            return failure
        parsed = parse_table(result.body)
        if not parsed:
            return fail(
                "unexpected_body",
                "The data response was not a table of rows, so no estimate was read.",
                "Retry later. The body was not passed on.",
            )
        header, rows = parsed
        if len(rows) == 0:
            return _absent_row(dataset, year, geography, http_204=False)
        if len(rows) != 1:
            return fail(
                "unexpected_body",
                "The data response had more than one geography row, so no estimate was read.",
                "Pass one geography. Nothing was averaged.",
            )
        built = sheet(header, rows[0], year, dataset, geography, variables)
        if built.get("empty_row"):
            built["estimates_read"] = False
            if dataset == "acs/acs1":
                built["sentence"] = (
                    "ACS 1-year vintage " + str(year)
                    + " returned no estimate for this geography. No county figure was substituted."
                )
            else:
                built["sentence"] = (
                    product_label(dataset) + " vintage " + str(year)
                    + " returned a geography row with empty estimate and margin cells, so no number was read."
                )
        return built


def _reject_absent_fips(service, dataset, year, geography):
    """HTTP 204 for a caller-supplied FIPS. A roster miss is not an empty estimate."""
    summary = geography.get("summary")
    if summary == "County":
        code = geography.get("county_fips")
        label = "County"
    elif summary == "Place":
        code = geography.get("place_fips")
        label = "Place"
    else:
        return None
    if not code or geography.get("name"):
        return None
    found = fips_roster(summary, str(geography.get("state_fips") or ""), str(code), service._fetch)
    if found.kind == "error":
        failure = dict(found.payload)
        failure["estimates_read"] = False
        return failure
    if found.kind != "none":
        return None
    return {
        "ok": True,
        "estimates_read": False,
        "empty_row": False,
        "resolved": False,
        "roster": "absent",
        "vintage_year": year,
        "dataset": dataset,
        "reads_as_calendar_year": False,
        "geography": _geo(geography),
        "figures": [],
        "sentence": (
            label + " FIPS " + str(code) + " in state " + str(geography.get("state_fips"))
            + " is absent from the Census roster, so no estimate was read."
        ),
    }


def _absent_row(dataset, year, geography, http_204: bool) -> dict:
    """A vintage with no geography row. The caller keeps the resolved geography."""
    if dataset == "acs/acs1":
        sentence = (
            "ACS 1-year vintage " + str(year)
            + " returned no estimate for this geography. No county figure was substituted."
        )
    elif http_204:
        sentence = (
            product_label(dataset) + " vintage " + str(year)
            + " returned HTTP 204 with an empty body, so no estimate was read for this geography. "
            + "No county figure was substituted."
        )
    else:
        sentence = "The ACS table returned a header and no geography row, so no estimate was read."
    return {
        "ok": True,
        "estimates_read": False,
        "empty_row": True,
        "resolved": True,
        "vintage_year": year,
        "dataset": dataset,
        "reads_as_calendar_year": False,
        "geography": _geo(geography),
        "figures": [],
        "sentence": sentence,
    }


def _limit(value, default, cap):
    if value is None:
        return default
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if not isinstance(value, int) or value < 1 or value > cap:
        return None
    return value


def _year_mismatch(args):
    if "year_a" not in args and "year_b" not in args:
        return None
    left_n = _plain_int(args.get("year_a", args.get("year")))
    right_n = _plain_int(args.get("year_b", args.get("year")))
    if left_n is None or right_n is None:
        return bad_year()
    year_n = _plain_int(args.get("year")) if "year" in args else left_n
    if left_n != right_n or year_n != left_n:
        return fail(
            "year_mismatch",
            "The two places use different ACS vintage years. This compare keeps one year.",
            "Pass one year for both places. No request was sent.",
        )
    return None


def _plain_int(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _geo(geography: dict) -> dict:
    return {
        "name": geography.get("name"),
        "summary": geography.get("summary"),
        "state_fips": geography.get("state_fips"),
        "state_abbr": geography.get("state_abbr"),
        "county_fips": geography.get("county_fips"),
        "place_fips": geography.get("place_fips"),
        "geoid": None if geography.get("geoid") is None else str(geography.get("geoid")),
    }


def _unresolved(payload: dict) -> dict:
    count = payload.get("match_count") or 0
    result = {
        "ok": True,
        "resolved": False,
        "estimates_read": False,
        "candidates": payload.get("candidates") or [],
        "match_count": count,
        "truncated": bool(payload.get("truncated")),
    }
    if count == 0:
        result["sentence"] = "No geography matched that name, so no estimate was read."
    elif payload.get("truncated"):
        result["sentence"] = "Several geographies matched that name. The candidate list was cut off, so no estimate was read. Pass one candidate name."
    elif payload.get("name_differs"):
        result["sentence"] = "The only hit has a longer name, so no estimate was read. Pass the full place name."
    elif count == 1:
        result["sentence"] = "No single geography was chosen, so no estimate was read."
    else:
        result["sentence"] = "Several geographies matched that name, so no estimate was read. Pass one candidate name."
    return result
