"""ACS metadata and estimate tables. Numbers keep their vintage year and variable id."""

from __future__ import annotations

import json
import re

from .errors import fail
from .geography import https
from .hosts import API_HOST, DATASETS, GROUP_OPEN_CAP, VARIABLE_CAP, YEAR_MIN
from .sentinels import parse_cell

VAR_ID = re.compile(r"^[A-Z]{1,3}[0-9]{3,5}_[0-9]{3}[EM]\Z")
GROUP_ID = re.compile(r"^[A-Z]{1,3}[0-9]{3,5}\Z")
FIPS2 = re.compile(r"^[0-9]{2}\Z")
FIPS3 = re.compile(r"^[0-9]{3}\Z")
FIPS5 = re.compile(r"^[0-9]{5}\Z")

BUNDLE = (
    ("B01003_001E", "Total population"),
    ("B19013_001E", "Median household income"),
    ("B01002_001E", "Median age"),
    ("B25001_001E", "Housing units"),
    ("B11001_001E", "Households"),
    ("B25077_001E", "Median home value"),
)

PRODUCT_LABEL = {
    "acs/acs5": "ACS 5-year",
    "acs/acs1": "ACS 1-year",
}


def dataset_ok(value: object):
    if value is None:
        return "acs/acs5"
    if not isinstance(value, str) or value not in DATASETS:
        return None
    return value


def parse_year(value, calendar_year: int):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        year = value
    elif isinstance(value, str) and value.isdigit():
        year = int(value)
    else:
        return None
    if year < YEAR_MIN or year > calendar_year:
        return None
    return year


def product_label(dataset: str) -> str:
    return PRODUCT_LABEL.get(dataset, dataset)


def descriptor_url(dataset: str, year: int) -> str:
    return https(API_HOST, "/data/" + str(year) + "/" + dataset + ".json", {})


def variables_link_for(dataset: str, year: int) -> str:
    return "https://" + API_HOST + "/data/" + str(year) + "/" + dataset + "/variables.json"


def catalog_url() -> str:
    return https(API_HOST, "/data.json", {})


def groups_url(dataset: str, year: int) -> str:
    return https(API_HOST, "/data/" + str(year) + "/" + dataset + "/groups.json", {})


def group_url(dataset: str, year: int, group: str) -> str:
    return https(API_HOST, "/data/" + str(year) + "/" + dataset + "/groups/" + group + ".json", {})


def variable_url(dataset: str, year: int, code: str) -> str:
    return https(API_HOST, "/data/" + str(year) + "/" + dataset + "/variables/" + code + ".json", {})


def geography_url(dataset: str, year: int) -> str:
    return https(API_HOST, "/data/" + str(year) + "/" + dataset + "/geography.json", {})


def data_url(dataset: str, year: int, variables: list, for_clause: str, in_clause, key: str) -> str:
    query = {"get": ",".join(variables), "for": for_clause, "key": key}
    if in_clause:
        query["in"] = in_clause
    return https(API_HOST, "/data/" + str(year) + "/" + dataset, query)


def siblings(variable_id: str) -> list:
    if not variable_id.endswith("E"):
        return [variable_id]
    stem = variable_id[:-1]
    return [variable_id, stem + "M", stem + "EA", stem + "MA"]


def bundle_columns() -> list:
    columns = ["NAME"]
    for variable_id, _label in BUNDLE:
        for column in siblings(variable_id):
            if column not in columns:
                columns.append(column)
    return columns


def figure(variable_id: str, label: str, year: int, dataset: str, cells: dict) -> dict:
    estimate_id = variable_id if variable_id.endswith("E") else variable_id[:-1] + "E"
    margin_id = estimate_id[:-1] + "M"
    if estimate_id in cells:
        estimate = parse_cell(cells.get(estimate_id))
    else:
        estimate = {"value": None, "annotation": None, "note": "The estimate column was not in this response."}
    if margin_id in cells:
        margin = parse_cell(cells.get(margin_id))
    else:
        margin = {"value": None, "annotation": None, "note": "The margin column was not in this response."}
    estimate_note = cells.get(estimate_id[:-1] + "EA") if estimate_id[:-1] + "EA" in cells else None
    margin_note = cells.get(margin_id[:-1] + "MA") if margin_id[:-1] + "MA" in cells else None
    if isinstance(estimate_note, str) and estimate_note and estimate["annotation"] is None and estimate["value"] is None:
        estimate = parse_cell(estimate_note)
    sentence = _figure_sentence(dataset, year, estimate_id, margin_id, estimate, margin)
    return {
        "variable_id": estimate_id,
        "margin_variable_id": margin_id,
        "label": label,
        "vintage_year": year,
        "dataset": dataset,
        "estimate": estimate["value"],
        "margin_of_error": margin["value"],
        "annotation": estimate["annotation"],
        "moe_annotation": margin["annotation"],
        "estimate_note": estimate["note"],
        "margin_note": margin["note"],
        "estimate_annotation_raw": None if estimate_note is None else str(estimate_note),
        "margin_annotation_raw": None if margin_note is None else str(margin_note),
        "reads_as_calendar_year": False,
        "sentence": sentence,
    }


def parse_table(body: bytes):
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, list) or not data:
        return None
    header = data[0]
    if not isinstance(header, list) or not header or not all(isinstance(cell, str) for cell in header):
        return None
    rows = []
    for row in data[1:]:
        if not isinstance(row, list) or len(row) != len(header):
            return None
        rows.append(row)
    return header, rows


def row_map(header: list, row: list) -> dict:
    return {header[index]: row[index] for index in range(len(header))}


def sheet(header: list, row: list, year: int, dataset: str, geography: dict, variables: list) -> dict:
    cells = row_map(header, row)
    figures = []
    for variable_id, label in variables:
        figures.append(figure(variable_id, label, year, dataset, cells))
    all_null = all(item["estimate"] is None and item["margin_of_error"] is None for item in figures)
    return {
        "ok": True,
        "estimates_read": True,
        "resolved": True,
        "empty_row": all_null,
        "vintage_year": year,
        "dataset": dataset,
        "product": product_label(dataset),
        "reads_as_calendar_year": False,
        "geography": _public_geo(geography),
        "name": cells.get("NAME") or geography.get("name"),
        "figures": figures,
        "sentence": _sheet_sentence(dataset, year, figures),
    }


def _figure_sentence(dataset: str, year: int, estimate_id: str, margin_id: str, estimate: dict, margin: dict) -> str:
    """Name a numeric margin as a margin of error. An annotation stays an annotation."""
    lead = product_label(dataset) + " vintage " + str(year) + " estimate " + estimate_id
    if margin.get("value") is not None:
        return lead + ", with margin of error " + margin_id + "."
    if margin.get("annotation"):
        return lead + ". Margin variable " + margin_id + " is the annotation " + str(margin["annotation"]) + "."
    return lead + ". Margin variable " + margin_id + " has no numeric margin."


def _sheet_sentence(dataset: str, year: int, figures: list) -> str:
    lead = product_label(dataset) + " vintage " + str(year)
    if figures and all(item.get("margin_of_error") is not None for item in figures):
        return lead + ". Each figure carries its variable id and margin of error."
    if any(item.get("moe_annotation") for item in figures):
        return lead + ". Each figure carries its variable id. A margin annotation stays an annotation."
    return lead + ". Each figure carries its variable id."


def _public_geo(geography: dict) -> dict:
    return {
        "name": geography.get("name"),
        "summary": geography.get("summary"),
        "state_fips": geography.get("state_fips"),
        "state_abbr": geography.get("state_abbr"),
        "county_fips": geography.get("county_fips"),
        "place_fips": geography.get("place_fips"),
        "geoid": None if geography.get("geoid") is None else str(geography.get("geoid")),
    }


# One row per descriptor identity. other_product is not the requested detailed table.
DESCRIPTOR_RESULTS = ("available", "unavailable", "other_product", "error")

# One finding per row whose c_dataset path is exactly the requested dataset.
# A sibling path, such as acs/acs5/cprofile, is not a finding.
EXACT_FINDINGS = ("available", "unavailable", "bad_link", "bad_row")

# First matching rule wins. Row order is not a rule. @id is not a rule.
DOCUMENT_RULES = (
    ("bad_row", "error"),
    ("mixed", "error"),
    ("available", "available"),
    ("unavailable", "unavailable"),
    ("bad_link", "other_product"),
    ("sibling", "other_product"),
    ("none", "error"),
)


def _classify_row(row, year: int, dataset: str) -> str:
    """Return an EXACT_FINDINGS name, sibling, or malformed."""
    if not isinstance(row, dict):
        return "malformed"
    parts = row.get("c_dataset")
    if not isinstance(parts, list) or not parts or not all(isinstance(part, str) and part for part in parts):
        return "malformed"
    if "/".join(parts) != dataset:
        return "sibling"
    if "c_isAvailable" not in row or "c_vintage" not in row or row.get("c_vintage") != year:
        return "bad_row"
    link = row.get("c_variablesLink")
    if isinstance(link, str) and link and link != variables_link_for(dataset, year):
        return "bad_link"
    if row.get("c_isAvailable") is True:
        return "available"
    if row.get("c_isAvailable") is False:
        return "unavailable"
    return "bad_row"


def _document_status(findings: list, saw_sibling: bool) -> str:
    """Apply DOCUMENT_RULES. Disagreeing exact rows are mixed."""
    if "bad_row" in findings:
        rule = "bad_row"
    elif ("available" in findings and ("unavailable" in findings or "bad_link" in findings)) or (
        "unavailable" in findings and "bad_link" in findings
    ):
        rule = "mixed"
    elif "available" in findings:
        rule = "available"
    elif "unavailable" in findings:
        rule = "unavailable"
    elif "bad_link" in findings:
        rule = "bad_link"
    elif saw_sibling:
        rule = "sibling"
    else:
        rule = "none"
    for name, status in DOCUMENT_RULES:
        if name == rule:
            return status
    return "error"


def parse_descriptor(body: bytes, year: int, dataset: str):
    """available only when some row is the requested detailed table.

    The first row is not the document. A comparison profile, PUMS file, subject
    table, or profile can lead the array while the detailed-tables row sits later.
    A sibling row does not cancel that detailed-tables row, and it does not
    become the year when the detailed-tables row is absent.
    """
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return "error"
    rows = data.get("dataset") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        return "error"
    findings = []
    saw_sibling = False
    for row in rows:
        kind = _classify_row(row, year, dataset)
        if kind == "sibling":
            saw_sibling = True
        elif kind in EXACT_FINDINGS:
            findings.append(kind)
    return _document_status(findings, saw_sibling)


def parse_catalog(body: bytes, query: str, limit: int):
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None
    rows = data.get("dataset") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return None
    matched = []
    needle = query.lower()
    for item in rows:
        if not isinstance(item, dict):
            continue
        parts = item.get("c_dataset")
        if not isinstance(parts, list):
            continue
        path = "/".join(str(part) for part in parts)
        title = str(item.get("title") or "")
        vintage = item.get("c_vintage")
        if needle and needle not in path.lower() and needle not in title.lower():
            continue
        matched.append({
            "vintage_year": vintage,
            "dataset": path,
            "title": title,
            "c_isAvailable": item.get("c_isAvailable"),
            "reads_as_calendar_year": False,
        })
    if needle:
        matched = _rank_catalog(matched)
    page = matched[:limit]
    return {
        "ok": True,
        "complete": len(page) == len(matched),
        "estimates_read": False,
        "query": query,
        "total": len(matched),
        "returned": len(page),
        "truncated": len(matched) > len(page),
        "datasets": page,
    }


def parse_geographies(body: bytes, year: int, dataset: str):
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None
    rows = data.get("fips") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return None
    levels = []
    for item in rows:
        if not isinstance(item, dict) or "name" not in item:
            continue
        levels.append({
            "name": item.get("name"),
            "geoLevelDisplay": item.get("geoLevelDisplay"),
            "referenceDate": item.get("referenceDate"),
            "requires": item.get("requires"),
            "wildcard": item.get("wildcard"),
        })
    return {
        "ok": True,
        "complete": True,
        "estimates_read": False,
        "vintage_year": year,
        "dataset": dataset,
        "reads_as_calendar_year": False,
        "total": len(levels),
        "levels": levels,
    }


def parse_variable(body: bytes, year: int, dataset: str):
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or "name" not in data:
        return None
    return {
        "ok": True,
        "estimates_read": False,
        "complete": True,
        "vintage_year": year,
        "dataset": dataset,
        "reads_as_calendar_year": False,
        "variable_id": data.get("name"),
        "label": data.get("label"),
        "concept": data.get("concept"),
        "group": data.get("group"),
        "attributes": data.get("attributes"),
        "predicateType": data.get("predicateType"),
    }


def parse_groups_index(body: bytes):
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None
    rows = data.get("groups") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return None
    groups = []
    for item in rows:
        if isinstance(item, dict) and item.get("name"):
            groups.append({"name": str(item.get("name")), "description": str(item.get("description") or "")})
    return groups


def parse_group_file(body: bytes):
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None
    variables = data.get("variables") if isinstance(data, dict) else None
    if not isinstance(variables, dict):
        return None
    rows = []
    for key, item in variables.items():
        if key == "NAME" or not isinstance(item, dict):
            continue
        rows.append({
            "variable_id": key,
            "label": item.get("label"),
            "concept": item.get("concept"),
            "predicateType": item.get("predicateType"),
        })
    return rows


def keyword_hits(groups: list, keyword: str) -> list:
    needle = keyword.lower()
    return [group for group in groups if needle in group["name"].lower() or needle in group["description"].lower()]


def _catalog_key(item: dict, index: int):
    """Exact acs/acs5 and acs/acs1 first, then a newer vintage, then catalog order."""
    year = item.get("vintage_year")
    if isinstance(year, bool) or not isinstance(year, int):
        year_key = -(10 ** 9)
    else:
        year_key = year
    exact = 0 if item.get("dataset") in DATASETS else 1
    return (exact, -year_key, index)


def _rank_catalog(matched: list) -> list:
    ranked = sorted(enumerate(matched), key=lambda pair: _catalog_key(pair[1], pair[0]))
    return [item for _index, item in ranked]


# Lower sorts first. A description that starts with the query, on a base table id, wins.
# A shorter description then beats a longer one that merely contains the words.
GROUP_RANK = ("starts", "base_id", "shorter", "name")


def _base_table_id(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Z]+[0-9]+", name))


def group_rank(group: dict, keyword: str):
    """Return the GROUP_RANK key. The caller opens groups in this order."""
    description = str(group.get("description") or "")
    name = str(group.get("name") or "")
    needle = keyword.casefold()
    starts = 0 if description.casefold().startswith(needle) else 1
    base = 0 if _base_table_id(name) else 1
    return (starts, base, len(description), name)


def rank_groups(groups: list, keyword: str) -> list:
    return sorted(groups, key=lambda group: group_rank(group, keyword))


def variable_rank(row: dict):
    """Total estimates first, then other estimates, then margins, then annotations."""
    vid = str(row.get("variable_id") or "")
    if vid.endswith("_001E"):
        tier = 0
    elif vid.endswith("E"):
        tier = 1
    elif vid.endswith("_001M"):
        tier = 2
    elif vid.endswith("M"):
        tier = 3
    else:
        tier = 4
    return (tier, vid)


def variables_in_group(rows: list, keyword: str) -> list:
    needle = keyword.lower()
    found = []
    for row in rows:
        blob = " ".join(str(row.get(field) or "") for field in ("variable_id", "label", "concept")).lower()
        if needle in blob:
            found.append(row)
    return found


def geography_clauses(args: dict):
    state = args.get("state_fips")
    county = args.get("county_fips")
    place = args.get("place_fips")
    if not isinstance(state, str) or not FIPS2.match(state):
        return None
    if place is not None:
        if not isinstance(place, str) or not FIPS5.match(place) or "*" in place:
            return None
        if county is not None:
            return None
        return {"for": "place:" + place, "in": "state:" + state, "summary": "Place", "state_fips": state, "county_fips": None, "place_fips": place}
    if county is not None:
        if not isinstance(county, str) or not FIPS3.match(county) or "*" in county:
            return None
        return {"for": "county:" + county, "in": "state:" + state, "summary": "County", "state_fips": state, "county_fips": county, "place_fips": None}
    if "*" in state:
        return None
    return {"for": "state:" + state, "in": None, "summary": "State", "state_fips": state, "county_fips": None, "place_fips": None}


def user_variables(value):
    if not isinstance(value, list) or not value or len(value) > VARIABLE_CAP:
        return None
    cleaned = []
    for item in value:
        if not isinstance(item, str) or not VAR_ID.match(item):
            return None
        if item not in cleaned:
            cleaned.append(item)
    return cleaned


def bad_year():
    return fail(
        "bad_args",
        "Year must be an ACS vintage from 2005 through the calendar year, and it is not a label for that calendar year.",
        "Pass the vintage year as an integer, or leave it out to use the newest listed vintage.",
    )


def bad_dataset():
    return fail(
        "bad_args",
        "Dataset must be acs/acs5 or acs/acs1.",
        "Pass acs/acs5 or acs/acs1. Other datasets are listed by census_datasets and are not fetched here.",
    )
