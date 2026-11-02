"""Resolve a place name to one Census geography, or to candidates."""

from __future__ import annotations

import json
import re
import unicodedata
import urllib.parse

from .errors import fail
from .hosts import (
    CANDIDATE_CAP,
    CDP_LAYER,
    COUNTY_FIELDS,
    COUNTY_LAYER,
    GEO_HOST,
    GEOCODER_PATH,
    NAME_MAX,
    PLACE_FIELDS,
    PLACES_LAYER,
    TIGER_HOST,
)
from .states import AMBIGUOUS_STATE_NAMES, BY_ABBR, BY_NAME, DC_ALIASES

_STREET = re.compile(r"^\d+\s+\S")
_COUNTY_WORD = re.compile(
    r"\b(county|parish|borough|census area|municipality)\b",
    re.IGNORECASE,
)


def https(host: str, path: str, query: dict) -> str:
    encoded = urllib.parse.urlencode(query)
    base = "https://" + host + path
    if encoded:
        return base + "?" + encoded
    return base


def quote_name(name: str):
    """Return a TIGERweb literal, or None when the name is not allowlisted."""
    if not name or len(name) > NAME_MAX:
        return None
    for char in name:
        if ord(char) < 32 or char in "\\;%_\"<>":
            return None
        if char in " '-." or char.isdigit() or char.isalpha():
            continue
        return None
    return name.replace("'", "''")


_TRAIL_COMMENT = re.compile(r"\s+(#|//).*$")

# Repeat until a pass changes nothing. Comment before semicolon, so
# "Austin, TX; # note" drops the note and then the semicolon.
# An interior semicolon is not a step, and it stays for quote_name to refuse.
CLEAN_STEPS = ("nfkc_strip", "trail_comment", "one_semicolon", "collapse_space")


def _apply_clean_step(text: str, step: str) -> str:
    if step == "nfkc_strip":
        return unicodedata.normalize("NFKC", text).strip()
    if step == "trail_comment":
        return _TRAIL_COMMENT.sub("", text).rstrip()
    if step == "one_semicolon":
        if text.endswith(";"):
            return text[:-1].rstrip()
        return text
    if step == "collapse_space":
        return " ".join(text.split())
    raise RuntimeError("unknown clean step")


def _clean(text: str) -> str:
    """Apply CLEAN_STEPS. Comment and semicolon repeat until the text stays."""
    text = _apply_clean_step(text, "nfkc_strip")
    while True:
        nxt = text
        for step in CLEAN_STEPS:
            if step == "nfkc_strip" or step == "collapse_space":
                continue
            nxt = _apply_clean_step(nxt, step)
        if nxt == text:
            break
        text = nxt
    return _apply_clean_step(text, "collapse_space")


def fold_marks(text: str) -> str:
    """Drop combining marks so San José and San Jose share a lookup key."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if unicodedata.category(char) != "Mn")


# Original spelling first, so an accented official name still matches.
# The folded spelling runs only after that lookup returns no row.
LOOKUP_FORMS = ("original", "folded")


def _lookup_forms(name: str) -> list:
    folded = fold_marks(name)
    if folded == name:
        return [name]
    return [name, folded]


# One row per place-input kind. resolve_place dispatches on this list.
INPUT_KINDS = (
    "address",
    "dc_alias",
    "state_suffix",
    "ambiguous_bare",
    "postal",
    "bare_state",
    "county",
    "official_place",
    "stem_place",
)

_OFFICIAL_SUFFIXES = (
    " city",
    " town",
    " village",
    " cdp",
)

# Asked spelling, then the Census NAME that spelling must select.
# The asked words stay whole. A class suffix is added beside them.
# Verified on TIGERweb layer 4 on 2026-11-06: Kansas City city,
# Carson City, New York city, Grant Town town, Elk Grove Village village.
PLACE_SPELLINGS = (
    ("Kansas City", "Kansas City city"),
    ("Oklahoma City", "Oklahoma City city"),
    ("Texas City", "Texas City city"),
    ("New York City", "New York city"),
    ("New York", "New York city"),
    ("Grant Town", "Grant Town town"),
    ("Elk Grove Village", "Elk Grove Village village"),
    ("Carson City", "Carson City"),
    ("Spring", "Spring CDP"),
    ("City of Austin", "Austin city"),
    ("Austin", "Austin city"),
)

# Official NAME values that must not satisfy the asked spelling.
PLACE_NONMATCHES = (
    ("Texas", "Texas City city"),
    ("Kansas City", "Kansas town"),
    ("York", "Yorktown city"),
    ("Spring", "Springtown city"),
)


def _official_place_name(name: str):
    """Class suffix when the phrase already ends in city, town, village, or CDP.

    The phrase is not rewritten. Kansas City stays Kansas City.
    """
    lowered = name.lower()
    for suffix in _OFFICIAL_SUFFIXES:
        if lowered.endswith(suffix) and len(name) > len(suffix):
            return suffix
    return None


def _state_suffix(folded: str):
    """(abbr, fips, name) when the text is a state name followed by State."""
    if not folded.endswith(" state"):
        return None
    return BY_NAME.get(folded[:-6].strip())


def _strip_place_words(name: str) -> str:
    lowered = name.lower()
    if lowered.startswith("city of "):
        name = name[8:].strip()
        lowered = name.lower()
    for suffix in (" census area", " municipality", " county", " parish", " borough", " city", " cdp", " town", " village"):
        if lowered.endswith(suffix) and len(name) > len(suffix):
            name = name[: -len(suffix)].strip()
            lowered = name.lower()
            break
    return name


class Resolved:
    def __init__(self, kind: str, payload: dict):
        self.kind = kind
        self.payload = payload


def classify_place(raw: str) -> str:
    """Return one INPUT_KINDS value. ``raw`` is already cleaned."""
    folded = raw.lower()
    if _STREET.match(raw):
        return "address"
    if folded in DC_ALIASES:
        return "dc_alias"
    if _state_suffix(folded) is not None:
        return "state_suffix"
    if folded in AMBIGUOUS_STATE_NAMES:
        return "ambiguous_bare"
    if len(raw) == 2 and raw.upper() in BY_ABBR:
        return "postal"
    if folded in BY_NAME:
        return "bare_state"
    if _COUNTY_WORD.search(raw):
        return "county"
    basename, _state = _split_state(raw)
    if _official_place_name(basename) is not None:
        return "official_place"
    return "stem_place"


def resolve_place(text: object, fetch) -> Resolved:
    if not isinstance(text, str):
        return Resolved("error", fail("bad_args", "Place takes a string.", "Pass the place name as text."))
    if any(ord(char) < 32 for char in text) or "<" in text or ">" in text:
        return Resolved("error", fail("bad_args", "Place contains a control character or markup.", "Pass the place name as plain text."))
    raw = _clean(text)
    if not raw or len(raw) > NAME_MAX:
        return Resolved("error", fail("bad_args", "Place is empty or longer than 180 characters.", "Pass one place name, up to 180 characters."))
    kind = classify_place(raw)
    if kind == "address":
        return _geocode(raw, fetch)
    if kind == "dc_alias":
        return _one_state("11", "District of Columbia", "DC")
    if kind == "state_suffix":
        abbr, fips, name = _state_suffix(raw.lower())
        return _one_state(fips, name, abbr)
    if kind == "ambiguous_bare":
        abbr, fips, name = BY_NAME[raw.lower()]
        return _state_and_places(name, abbr, fips, fetch)
    if kind == "postal":
        fips, name = BY_ABBR[raw.upper()]
        return _one_state(fips, name, raw.upper())
    if kind == "bare_state":
        abbr, fips, name = BY_NAME[raw.lower()]
        return _bare_state(name, abbr, fips, fetch)
    if kind == "county":
        found = _tiger_counties(raw, fetch)
        # borough, municipality, and census area are also place classes.
        # A county miss tries the place layers before reporting no geography.
        if found.kind == "none" and _place_class_on_county(_split_state(raw)[0]):
            return _tiger_places(raw, fetch, force_candidates=False)
        return found
    return _tiger_places(raw, fetch, force_candidates=False)


def _one_state(fips: str, name: str, abbr: str) -> Resolved:
    return Resolved("one", {
        "summary": "State",
        "name": name,
        "state_fips": fips,
        "state_abbr": abbr,
        "county_fips": None,
        "place_fips": None,
        "geoid": fips,
        "for": "state:" + fips,
        "in": None,
    })


def _geocode(text: str, fetch) -> Resolved:
    url = https(GEO_HOST, GEOCODER_PATH, {
        "address": text,
        "benchmark": "Public_AR_Current",
        "vintage": "Current_Current",
        "format": "json",
    })
    result = fetch(url, cap="geo")
    if result.kind != "ok":
        return Resolved("error", _transport(result))
    try:
        data = json.loads(result.body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return Resolved("error", _shape())
    if isinstance(data, dict) and "error" in data and "result" not in data:
        return Resolved("error", _rejected())
    matches = data.get("result", {}).get("addressMatches") if isinstance(data, dict) else None
    if not isinstance(matches, list):
        return Resolved("error", _shape())
    if len(matches) == 0:
        return Resolved("none", {"candidates": [], "match_count": 0, "estimates_read": False, "resolved": False})
    if len(matches) != 1 or not isinstance(matches[0], dict):
        return Resolved("candidates", {
            "candidates": [_match_candidate(item) for item in matches[:CANDIDATE_CAP] if isinstance(item, dict)],
            "match_count": len(matches),
            "truncated": len(matches) > CANDIDATE_CAP,
            "estimates_read": False,
            "resolved": False,
        })
    geos = matches[0].get("geographies")
    if not isinstance(geos, dict):
        return Resolved("error", _shape())
    incorporated = _layer_features(geos, "incorporated places")
    cdp = _layer_features(geos, "census designated places")
    if len(incorporated) == 1:
        return _from_feature(incorporated[0], "Incorporated Place", county=_first_feature(geos, "counties"))
    if len(incorporated) > 1:
        return _address_place_candidates(incorporated, cdp)
    if len(cdp) == 1:
        return _from_feature(cdp[0], "Census Designated Place", county=_first_feature(geos, "counties"))
    if len(cdp) > 1:
        return _feature_candidates(cdp, "Census Designated Place")
    return Resolved("none", {"candidates": [], "match_count": 0, "estimates_read": False, "resolved": False})


def _layer_features(geos: dict, needle: str) -> list:
    found = []
    for key, value in geos.items():
        if needle in str(key).lower() and isinstance(value, list):
            found.extend(item for item in value if isinstance(item, dict))
    return found


def _first_feature(geos: dict, needle: str):
    rows = _layer_features(geos, needle)
    return rows[0] if rows else None


def _attr(feature: dict, *names: str):
    for name in names:
        if name in feature:
            return feature[name]
        for key, value in feature.items():
            if str(key).upper() == name.upper():
                return value
    return None


def _fips(value, width: int):
    if value is None:
        return None
    text = str(value).strip()
    if not text.isdigit():
        return None
    return text.zfill(width)[-width:]


def _from_feature(feature: dict, summary: str, county=None) -> Resolved:
    state = _fips(_attr(feature, "STATE"), 2)
    place = _fips(_attr(feature, "PLACE"), 5)
    name = _attr(feature, "NAME") or summary
    county_fips = _fips(_attr(feature, "COUNTY"), 3)
    if county_fips is None and isinstance(county, dict):
        county_fips = _fips(_attr(county, "COUNTY"), 3)
    if not state or not place:
        return Resolved("error", _shape())
    return Resolved("one", {
        "summary": summary,
        "name": str(name),
        "state_fips": state,
        "state_abbr": _abbr(state),
        "county_fips": county_fips,
        "place_fips": place,
        "geoid": _attr(feature, "GEOID") or (state + place),
        "for": "place:" + place,
        "in": "state:" + state,
    })


def _address_place_candidates(incorporated: list, cdp: list) -> Resolved:
    pairs = [(item, "Incorporated Place") for item in incorporated]
    pairs.extend((item, "Census Designated Place") for item in cdp)
    rows = [_candidate_row(feature, summary) for feature, summary in pairs[:CANDIDATE_CAP]]
    return Resolved("candidates", {
        "candidates": rows,
        "match_count": len(pairs),
        "truncated": len(pairs) > CANDIDATE_CAP,
        "estimates_read": False,
        "resolved": False,
    })


def _feature_candidates(features: list, summary: str) -> Resolved:
    rows = []
    for feature in features[:CANDIDATE_CAP]:
        rows.append(_candidate_row(feature, summary))
    return Resolved("candidates", {
        "candidates": rows,
        "match_count": len(features),
        "truncated": len(features) > CANDIDATE_CAP,
        "estimates_read": False,
        "resolved": False,
    })


def _candidate_row(feature: dict, summary: str) -> dict:
    state = _fips(_attr(feature, "STATE"), 2)
    return {
        "name": str(_attr(feature, "NAME") or ""),
        "summary": summary,
        "state_fips": state,
        "state_abbr": _abbr(state) if state else None,
        "county_fips": _fips(_attr(feature, "COUNTY"), 3),
        "place_fips": _fips(_attr(feature, "PLACE"), 5),
        "geoid": None if _attr(feature, "GEOID") is None else str(_attr(feature, "GEOID")),
    }


def _match_candidate(item: dict) -> dict:
    return {"name": str(item.get("matchedAddress") or ""), "summary": "Address match"}


def _abbr(fips: str):
    for abbr, (code, _name) in BY_ABBR.items():
        if code == fips:
            return abbr
    return None


def _split_state(text: str):
    if "," not in text:
        return text, None
    left, right = text.rsplit(",", 1)
    right = right.strip()
    if len(right) == 2 and right.upper() in BY_ABBR:
        return left.strip(), BY_ABBR[right.upper()][0]
    if right.lower() in BY_NAME:
        return left.strip(), BY_NAME[right.lower()][1]
    return text, None


_EXACT_SUFFIXES = (
    " city",
    " town",
    " village",
    " borough",
    " municipality",
    " CDP",
    " census area",
)
_EXACT_PREFIXES = (
    "City of ",
    "Town of ",
    "Village of ",
    "Borough of ",
)


def _upper_literal(name: str):
    """TIGERweb compares case-sensitively. The stored NAME keeps its own case."""
    quoted = quote_name(name)
    if not quoted:
        return None
    return quoted.upper()


def _exact_where(name: str):
    literals = [name]
    for suffix in _EXACT_SUFFIXES:
        literals.append(name + suffix)
    for prefix in _EXACT_PREFIXES:
        literals.append(prefix + name)
    quoted = []
    for item in literals:
        safe = _upper_literal(item)
        if not safe:
            return None
        quoted.append("'" + safe + "'")
    return "UPPER(NAME) IN (" + ",".join(quoted) + ")"


def _collision_hit(found: Resolved, name: str) -> bool:
    """True when an exact official name is present, or the page was cut off."""
    if found.kind == "none":
        return False
    if found.kind == "one":
        return _labels_match(str(found.payload.get("name") or ""), name)
    if found.kind != "candidates":
        return True
    if found.payload.get("truncated"):
        return True
    rows = found.payload.get("candidates") or []
    if int(found.payload.get("match_count") or 0) != len(rows):
        return True
    return any(_labels_match(str(row.get("name") or ""), name) for row in rows)


def _bare_state(name: str, abbr: str, fips: str, fetch) -> Resolved:
    """A bare state name is the state only after an exact place and CDP check."""
    where = _exact_where(name)
    if not where:
        return Resolved("error", fail("bad_args", "Place uses characters the geography search does not accept.", "Use letters, digits, spaces, hyphens, periods, and apostrophes."))
    for path in (PLACES_LAYER, CDP_LAYER):
        found = _query_layer(path, where, fetch)
        if found.kind == "error":
            return found
        if _collision_hit(found, name):
            return _state_and_places(name, abbr, fips, fetch)
    return _one_state(fips, name, abbr)


def _with_state(where: str, state) -> str:
    if state:
        return where + " AND STATE='" + state + "'"
    return where


_PLACE_CLASS_ON_COUNTY = (
    " borough",
    " municipality",
    " census area",
)


def _place_class_on_county(name: str) -> bool:
    lowered = name.lower()
    for suffix in _PLACE_CLASS_ON_COUNTY:
        if lowered.endswith(suffix) and len(name) > len(suffix):
            return True
    return False


def _place_cores(phrase: str) -> list:
    """Phrase, plus the remainder after one leading class prefix."""
    cores = [phrase]
    lowered = phrase.lower()
    for prefix in _EXACT_PREFIXES:
        if lowered.startswith(prefix.lower()) and len(phrase) > len(prefix):
            rest = phrase[len(prefix):].strip()
            if rest and rest not in cores:
                cores.append(rest)
            break
    return cores


def place_literals(phrase: str) -> list:
    """Exact TIGER NAME spellings. The phrase is not shortened first."""
    literals = []
    seen = set()
    for core in _place_cores(phrase):
        items = [core]
        items.extend(core + suffix for suffix in _EXACT_SUFFIXES)
        items.extend(prefix + core for prefix in _EXACT_PREFIXES)
        for item in items:
            key = item.casefold()
            if key in seen:
                continue
            seen.add(key)
            literals.append(item)
    return literals


def place_name_equal(official: str, asked: str) -> bool:
    """True when the Census NAME is the phrase or that phrase plus one class."""
    left = fold_marks(official).casefold()
    for core in _place_cores(asked):
        right = fold_marks(core).casefold()
        if left == right:
            return True
        for suffix in _EXACT_SUFFIXES:
            if left == (right + suffix).casefold():
                return True
        for prefix in _EXACT_PREFIXES:
            if left == (prefix + core).casefold():
                return True
    return False


def _exact_place_where(phrase: str):
    quoted = []
    seen = set()
    for item in place_literals(phrase):
        safe = _upper_literal(item)
        if not safe:
            return None
        if safe in seen:
            continue
        seen.add(safe)
        quoted.append("'" + safe + "'")
    if not quoted:
        return None
    return "UPPER(NAME) IN (" + ",".join(quoted) + ")"


def _query_both(where: str, state, fetch) -> Resolved:
    scoped = _with_state(where, state)
    incorporated = _query_layer(PLACES_LAYER, scoped, fetch)
    if incorporated.kind == "error":
        return incorporated
    cdp = _query_layer(CDP_LAYER, scoped, fetch)
    if cdp.kind == "error":
        return cdp
    return _merge_places(incorporated, cdp)


def _empty_places() -> Resolved:
    return Resolved("none", {"candidates": [], "match_count": 0, "estimates_read": False, "resolved": False})


def _filter_exact(found: Resolved, asked: str) -> Resolved:
    """Keep rows whose NAME matches the phrase. A cut-off page stays cut off."""
    if found.kind in ("error", "none"):
        return found
    if found.payload.get("truncated"):
        return found
    if found.kind == "one":
        name = str(found.payload.get("name") or "")
        if place_name_equal(name, asked):
            return found
        return _empty_places()
    rows = [
        row for row in (found.payload.get("candidates") or [])
        if place_name_equal(str(row.get("name") or ""), asked)
    ]
    if not rows:
        return _empty_places()
    if len(rows) == 1:
        return _one_from_row(rows[0])
    return Resolved("candidates", {
        "candidates": rows,
        "match_count": len(rows),
        "truncated": False,
        "estimates_read": False,
        "resolved": False,
    })


def _reject_place(where) -> Resolved:
    if where:
        return None
    return Resolved("error", fail(
        "bad_args",
        "Place uses characters the geography search does not accept.",
        "Use letters, digits, spaces, hyphens, periods, and apostrophes.",
    ))


def _exact_place_search(basename: str, state, fetch) -> Resolved:
    last = _empty_places()
    for form in _lookup_forms(basename):
        where = _exact_place_where(form)
        rejected = _reject_place(where)
        if rejected is not None:
            return rejected
        found = _query_both(where, state, fetch)
        if found.kind == "error":
            return found
        filtered = _filter_exact(found, form)
        if filtered.kind == "error":
            return filtered
        if filtered.kind != "none":
            return filtered
        last = filtered
    return last


def _prefix_place_search(basename: str, state, fetch):
    """Return (found, form). form is the spelling whose prefix query returned rows."""
    last = _empty_places()
    for form in _lookup_forms(basename):
        found = _query_places(form, state, fetch)
        if found.kind == "error":
            return found, form
        if found.kind != "none":
            return found, form
        last = found
    return last, basename


def _query_places(basename: str, state, fetch) -> Resolved:
    quoted = _upper_literal(basename)
    if not quoted:
        return Resolved("error", fail("bad_args", "Place uses characters the geography search does not accept.", "Use letters, digits, spaces, hyphens, periods, and apostrophes."))
    where = _with_state("UPPER(NAME) LIKE '" + quoted + "%'", state)
    incorporated = _query_layer(PLACES_LAYER, where, fetch)
    if incorporated.kind == "error":
        return incorporated
    cdp = _query_layer(CDP_LAYER, where, fetch)
    if cdp.kind == "error":
        return cdp
    return _merge_places(incorporated, cdp)


def _tiger_places(text: str, fetch, force_candidates: bool) -> Resolved:
    """Exact class-suffix names first. A prefix query runs only after a miss."""
    basename, state = _split_state(text)
    found = _exact_place_search(basename, state, fetch)
    if found.kind == "error":
        return found
    if found.kind == "none":
        found, asked = _prefix_place_search(basename, state, fetch)
        if found.kind == "error" or found.kind == "none":
            return found
        if force_candidates and found.kind == "one":
            return _as_candidates(found.payload, 1, False)
        if force_candidates:
            return found
        return choose_stem(found, asked, place_name_equal)
    if force_candidates and found.kind == "one":
        return _as_candidates(found.payload, 1, False)
    if force_candidates:
        return found
    return found


def _labels_match(official: str, asked: str) -> bool:
    left = fold_marks(_strip_place_words(official)).casefold()
    right = fold_marks(asked).casefold()
    return left == right


def _demote_if_inexact(found: Resolved, asked: str, match=None) -> Resolved:
    """A single longer name stays a candidate, so no estimate is chosen."""
    match = match or _labels_match
    if found.kind != "one":
        return found
    official = str(found.payload.get("name") or "")
    if match(official, asked):
        return found
    payload = _as_candidates(found.payload, 1, False).payload
    payload["name_differs"] = True
    return Resolved("candidates", payload)


# One row per list shape. choose_stem is this table.
STEM_RESULTS = (
    "none",
    "one_exact",
    "one_exact_among_prefixes",
    "many_exact",
    "one_longer",
    "many_longer",
    "truncated",
)


def stem_result(rows, asked: str, truncated: bool, match_count: int, match=None) -> str:
    """Classify a complete name list. A short page is truncated even with one exact name."""
    match = match or _labels_match
    if truncated or match_count != len(rows):
        return "truncated"
    if not rows:
        return "none"
    exact = [row for row in rows if match(str(row.get("name") or ""), asked)]
    if len(exact) == 1 and len(rows) == 1:
        return "one_exact"
    if len(exact) == 1:
        return "one_exact_among_prefixes"
    if len(exact) > 1:
        return "many_exact"
    if len(rows) == 1:
        return "one_longer"
    return "many_longer"


def choose_stem(found: Resolved, asked: str, match=None) -> Resolved:
    """Apply STEM_RESULTS. Exactly one official name is the place. A cut-off list is not."""
    match = match or _labels_match
    if found.kind in ("error", "none"):
        return found
    if found.kind == "one":
        return _demote_if_inexact(found, asked, match)
    if found.kind != "candidates":
        return found
    rows = list(found.payload.get("candidates") or [])
    truncated = bool(found.payload.get("truncated"))
    match_count = int(found.payload.get("match_count") or len(rows))
    shape = stem_result(rows, asked, truncated, match_count, match)
    if shape in ("one_exact", "one_exact_among_prefixes"):
        exact = [row for row in rows if match(str(row.get("name") or ""), asked)]
        return _one_from_row(exact[0])
    if shape == "one_longer":
        payload = dict(found.payload)
        payload["name_differs"] = True
        return Resolved("candidates", payload)
    return found


def _merge_places(left: Resolved, right: Resolved) -> Resolved:
    if left.kind == "error":
        return left
    if right.kind == "error":
        return right
    if left.kind == "none":
        return right
    if right.kind == "none":
        return left
    rows = []
    count = 0
    truncated = False
    for found in (left, right):
        if found.kind == "one":
            rows.append(_candidate_row_from_one(found.payload))
            count += 1
        elif found.kind == "candidates":
            more = list(found.payload.get("candidates") or [])
            rows.extend(more)
            count += int(found.payload.get("match_count") or len(more))
            truncated = truncated or bool(found.payload.get("truncated"))
    truncated = truncated or len(rows) > CANDIDATE_CAP
    shown = rows[:CANDIDATE_CAP]
    if count == 1 and len(shown) == 1 and not truncated:
        return _one_from_row(shown[0])
    return Resolved("candidates", {
        "candidates": shown,
        "match_count": count,
        "truncated": truncated,
        "estimates_read": False,
        "resolved": False,
    })


def _one_from_row(row: dict) -> Resolved:
    state = row.get("state_fips")
    place = row.get("place_fips")
    county = row.get("county_fips")
    summary = str(row.get("summary") or "")
    if place:
        if not state:
            return Resolved("error", _shape())
        return Resolved("one", {
            "summary": summary or "Incorporated Place",
            "name": row.get("name"),
            "state_fips": state,
            "state_abbr": row.get("state_abbr"),
            "county_fips": county,
            "place_fips": place,
            "geoid": row.get("geoid") or (str(state) + str(place)),
            "for": "place:" + str(place),
            "in": "state:" + str(state),
        })
    if summary == "County" and county and state:
        return Resolved("one", {
            "summary": "County",
            "name": row.get("name"),
            "state_fips": state,
            "state_abbr": row.get("state_abbr"),
            "county_fips": county,
            "place_fips": None,
            "geoid": row.get("geoid") or (str(state) + str(county)),
            "for": "county:" + str(county),
            "in": "state:" + str(state),
        })
    return Resolved("error", _shape())


def _state_and_places(name: str, abbr: str, fips: str, fetch) -> Resolved:
    found = _tiger_places(name, fetch, force_candidates=True)
    state_row = {
        "name": name,
        "summary": "State",
        "state_fips": fips,
        "state_abbr": abbr,
        "county_fips": None,
        "place_fips": None,
        "geoid": fips,
    }
    if found.kind == "error":
        return found
    rows = []
    if found.kind == "candidates":
        rows.extend(found.payload.get("candidates") or [])
        truncated = bool(found.payload.get("truncated"))
        count = int(found.payload.get("match_count") or len(rows))
    elif found.kind == "one":
        rows.append(_candidate_row_from_one(found.payload))
        truncated = False
        count = 1
    else:
        truncated = False
        count = 0
    rows.append(state_row)
    truncated = truncated or len(rows) > CANDIDATE_CAP
    shown = rows[:CANDIDATE_CAP]
    if state_row not in shown:
        shown = (shown[:-1] if shown else []) + [state_row]
    return Resolved("candidates", {
        "candidates": shown,
        "match_count": count + 1,
        "truncated": truncated,
        "estimates_read": False,
        "resolved": False,
    })


def _candidate_row_from_one(payload: dict) -> dict:
    return {
        "name": payload.get("name"),
        "summary": payload.get("summary"),
        "state_fips": payload.get("state_fips"),
        "state_abbr": payload.get("state_abbr"),
        "county_fips": payload.get("county_fips"),
        "place_fips": payload.get("place_fips"),
        "geoid": payload.get("geoid"),
    }


def _tiger_counties(text: str, fetch) -> Resolved:
    basename, state = _split_state(text)
    basename = _strip_place_words(basename)
    found = None
    asked = basename
    for form in _lookup_forms(basename):
        quoted = _upper_literal(form)
        if not quoted:
            return Resolved("error", fail("bad_args", "County uses characters the geography search does not accept.", "Use letters, digits, spaces, hyphens, periods, and apostrophes."))
        where = _with_state("UPPER(NAME) LIKE '" + quoted + "%'", state)
        found = _query_layer(COUNTY_LAYER, where, fetch, summary="County", fields=COUNTY_FIELDS)
        if found.kind == "error":
            return found
        if found.kind != "none":
            asked = form
            break
    else:
        return found
    return choose_stem(found, asked)


def _query_layer(path: str, where: str, fetch, summary: str = "", fields: str = PLACE_FIELDS) -> Resolved:
    url = https(TIGER_HOST, path, {
        "where": where,
        "outFields": fields,
        "returnGeometry": "false",
        "f": "json",
        "resultRecordCount": str(CANDIDATE_CAP + 1),
    })
    result = fetch(url, cap="geo")
    if result.kind != "ok":
        return Resolved("error", _transport(result))
    try:
        data = json.loads(result.body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return Resolved("error", _shape())
    if not isinstance(data, dict):
        return Resolved("error", _shape())
    if "error" in data and "features" not in data:
        return Resolved("error", _rejected())
    features = data.get("features")
    if not isinstance(features, list):
        return Resolved("error", _shape())
    rows = []
    for feature in features:
        if isinstance(feature, dict) and isinstance(feature.get("attributes"), dict):
            rows.append(feature["attributes"])
    label = summary or ("Incorporated Place" if path == PLACES_LAYER else "Census Designated Place" if path == CDP_LAYER else "County")
    if len(rows) == 0:
        return Resolved("none", {"candidates": [], "match_count": 0, "estimates_read": False, "resolved": False})
    if len(rows) == 1:
        feature = rows[0]
        if label == "County":
            return _one_county(feature)
        return _from_feature(feature, label)
    payload_rows = [_candidate_row(feature, label) for feature in rows[:CANDIDATE_CAP]]
    return Resolved("candidates", {
        "candidates": payload_rows,
        "match_count": len(rows),
        "truncated": len(rows) > CANDIDATE_CAP or bool(data.get("exceededTransferLimit")),
        "estimates_read": False,
        "resolved": False,
    })


def fips_roster(summary: str, state: str, code: str, fetch) -> Resolved:
    """Look up one county or place FIPS. ``none`` means the roster has no row."""
    if not state.isdigit() or not code.isdigit():
        return Resolved("error", fail(
            "bad_args",
            "Geography FIPS must be digits.",
            "Pass the state FIPS and the county or place FIPS.",
        ))
    if summary == "County":
        where = "STATE='" + state + "' AND COUNTY='" + code + "'"
        return _query_layer(COUNTY_LAYER, where, fetch, summary="County", fields=COUNTY_FIELDS)
    if summary == "Place":
        where = "STATE='" + state + "' AND PLACE='" + code + "'"
        found = _query_layer(PLACES_LAYER, where, fetch)
        if found.kind == "error" or found.kind != "none":
            return found
        return _query_layer(CDP_LAYER, where, fetch)
    return Resolved("error", fail(
        "bad_args",
        "The roster check needs a county or a place.",
        "Pass a county FIPS or a place FIPS.",
    ))


def _one_county(feature: dict) -> Resolved:
    state = _fips(_attr(feature, "STATE"), 2)
    county = _fips(_attr(feature, "COUNTY"), 3)
    if not state or not county:
        return Resolved("error", _shape())
    name = str(_attr(feature, "NAME") or "County")
    return Resolved("one", {
        "summary": "County",
        "name": name,
        "state_fips": state,
        "state_abbr": _abbr(state),
        "county_fips": county,
        "place_fips": None,
        "geoid": _attr(feature, "GEOID") or (state + county),
        "for": "county:" + county,
        "in": "state:" + state,
    })


def _as_candidates(payload: dict, count: int, truncated: bool) -> Resolved:
    return Resolved("candidates", {
        "candidates": [_candidate_row_from_one(payload)],
        "match_count": count,
        "truncated": truncated,
        "estimates_read": False,
        "resolved": False,
    })


def _rejected() -> dict:
    return fail(
        "unexpected_body",
        "The geography service rejected the query, so no estimate was read.",
        "No number was filled in.",
    )


def _shape() -> dict:
    return fail(
        "unexpected_body",
        "The geography response did not have the expected JSON shape, so no estimate was read.",
        "Retry later. No number was filled in.",
    )


def _transport(result) -> dict:
    from .http_client import as_failure
    failure = as_failure(result)
    if failure is not None:
        return failure
    return _shape()
