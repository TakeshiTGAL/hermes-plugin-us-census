"""Failure objects the tools return. One dict shape, typed error_type."""

from __future__ import annotations


def fail(error_type: str, error: str, next_step: str) -> dict:
    return {"ok": False, "error_type": error_type, "error": error, "next_step": next_step}


KEY_URL = "https://www.census.gov/data/developers/guidance/api-user-guide.API_Key.html"

KEY_UNSET = (
    "Census data queries need a free CENSUS_API_KEY. "
    "The key is unset in this profile, so no estimate was read. "
    "Add the key to this profile's .env. "
    "Request one at " + KEY_URL
)

KEY_HTML = (
    "Census data queries need a free CENSUS_API_KEY. "
    "The data server answered HTTP 200 with an HTML page titled Missing Key, so no estimate was read. "
    "Add the key to this profile's .env. "
    "Request one at " + KEY_URL
)

KEY_REDIRECT_MISSING = (
    "Census data queries need a free CENSUS_API_KEY. "
    "The data server redirected to its Missing Key page, and that redirect was not followed, so no estimate was read. "
    "Add the key to this profile's .env. "
    "Request one at " + KEY_URL
)

KEY_REJECTED = (
    "Census data queries need a free CENSUS_API_KEY. "
    "The data server redirected to its Invalid Key page, and that redirect was not followed, so no estimate was read. "
    "Replace the key in this profile's .env. "
    "Request one at " + KEY_URL
)

KEY_REJECTED_HTML = (
    "Census data queries need a free CENSUS_API_KEY. "
    "The data server answered HTTP 200 with an HTML page titled Invalid Key, so no estimate was read. "
    "Replace the key in this profile's .env. "
    "Request one at " + KEY_URL
)

KEY_NEXT = "Add CENSUS_API_KEY to this profile's .env. Request one at the official Census key page."
KEY_REJECTED_NEXT = "Replace CENSUS_API_KEY in this profile's .env. Request one at the official Census key page."
