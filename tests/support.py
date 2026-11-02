"""Load the plugin the way Hermes does, and stand in for Census HTTP."""

from __future__ import annotations

import datetime
import importlib.util
import json
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = "census-test-key-should-not-leak"
CLOCK = datetime.datetime(2026, 11, 3, tzinfo=datetime.timezone.utc)


def load_plugin():
    name = "us_census_plugin"
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(
        name,
        str(ROOT / "__init__.py"),
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


plugin = load_plugin()
HttpResult = plugin.http_client.HttpResult


def ok_body(payload, truncated=False):
    if isinstance(payload, bytes):
        body = payload
    else:
        body = json.dumps(payload).encode("utf-8")
    return HttpResult("ok", 200, body, "application/json", truncated)


def http_status(code):
    return HttpResult("http_status", status=code, body=b"")


class World:
    def __init__(self):
        self.calls = []
        self.keys = []
        self.plain = []
        self.routes = []

    def add(self, needle, result):
        self.routes.append((needle, result))
        return self

    def transport(self, url, cap="data", timeout=20):
        parts = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qs(parts.query)
        self.calls.append({
            "host": parts.hostname,
            "path": parts.path,
            "cap": cap,
            "names": sorted(query),
            "query": query,
        })
        if "key" in query:
            self.keys.append(query["key"][0])
        elif "key=" not in url:
            self.plain.append(urllib.parse.unquote_plus(url))
        matches = [(needle, result) for needle, result in self.routes if needle in url]
        if not matches:
            raise AssertionError("unmapped " + parts.path)
        _needle, result = max(matches, key=lambda item: len(item[0]))
        return result

    def paths(self):
        return [item["path"] for item in self.calls]

    def hosts(self):
        return [item["host"] for item in self.calls]


def make_service(world, reader=None):
    if reader is None:
        reader = lambda: (None, "missing")
    return plugin.service.Census(
        transport=world.transport,
        key_reader=reader,
        clock=lambda: CLOCK,
    )


def with_key(world, value=SAMPLE):
    return make_service(world, reader=lambda: (value, None))


def austin_row():
    return {
        "NAME": "Austin city",
        "STATE": "48",
        "PLACE": "05000",
        "COUNTY": "453",
        "GEOID": "4805000",
    }


def tiger(rows):
    return {"features": [{"attributes": row} for row in rows]}


def descriptor(year, available=True, dataset="acs/acs5"):
    return {
        "dataset": [{
            "c_vintage": year,
            "c_isAvailable": available,
            "c_dataset": dataset.split("/"),
            "title": "ACS Detailed Tables",
        }]
    }


def variable_doc():
    return {
        "name": "B01003_001E",
        "label": "Estimate!!Total",
        "concept": "Total Population",
        "group": "B01003",
        "attributes": "B01003_001EA,B01003_001M,B01003_001MA",
        "predicateType": "int",
    }


def one_table(value="100", margin="5"):
    return [[
        "NAME", "B01003_001E", "B01003_001M", "B01003_001EA", "B01003_001MA",
    ], [
        "Texas", value, margin, None, None,
    ]]


def bundle_table(value="100", margin="5", name="Austin city, Texas"):
    columns = plugin.acs.bundle_columns()
    row = []
    for column in columns:
        if column == "NAME":
            row.append(name)
        elif column.endswith("E"):
            row.append(value)
        elif column.endswith("M"):
            row.append(margin)
        else:
            row.append(None)
    return [columns, row]


def place_routes(world, rows, cdp_rows=None):
    world.add("MapServer/4", ok_body(tiger(rows)))
    world.add("MapServer/5", ok_body(tiger([] if cdp_rows is None else cdp_rows)))
    return world
