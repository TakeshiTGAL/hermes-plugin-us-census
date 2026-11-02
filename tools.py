"""Tool handlers. Each returns a JSON string and does not raise."""

from __future__ import annotations

import json

from .errors import fail
from .service import Census

SERVICE = Census()
HANDLERS = {}


def _run(method, args):
    try:
        result = method(args)
    except Exception:
        result = fail(
            "internal",
            "The request stopped before a figure was read.",
            "Retry. No estimate was saved.",
        )
    if not isinstance(result, dict):
        result = fail(
            "internal",
            "The request stopped before a figure was read.",
            "Retry. No estimate was saved.",
        )
    return json.dumps(result, ensure_ascii=True)


def census_datasets(args, **_kwargs):
    return _run(SERVICE.datasets, args)


def census_geographies(args, **_kwargs):
    return _run(SERVICE.geographies, args)


def census_variables(args, **_kwargs):
    return _run(SERVICE.variables, args)


def census_place(args, **_kwargs):
    return _run(SERVICE.place, args)


def census_get(args, **_kwargs):
    return _run(SERVICE.get, args)


def census_compare(args, **_kwargs):
    return _run(SERVICE.compare, args)


HANDLERS["census_datasets"] = census_datasets
HANDLERS["census_geographies"] = census_geographies
HANDLERS["census_variables"] = census_variables
HANDLERS["census_place"] = census_place
HANDLERS["census_get"] = census_get
HANDLERS["census_compare"] = census_compare
