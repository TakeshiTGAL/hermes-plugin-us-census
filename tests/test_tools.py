"""Tool behavior with mocked Census HTTP."""

from __future__ import annotations

import json
import urllib.parse

from tests.support import (
    CLOCK,
    SAMPLE,
    World,
    austin_row,
    bundle_table,
    descriptor,
    http_status,
    make_service,
    ok_body,
    one_table,
    place_routes,
    plugin,
    tiger,
    variable_doc,
    with_key,
)

HttpResult = plugin.http_client.HttpResult


def _place(world, **extra):
    args = {"place": "Austin, TX", "year": 2024}
    args.update(extra)
    return with_key(world).place(args)


def test_place_returns_one_sheet_with_year_variable_and_margin():
    world = World()
    place_routes(world, [austin_row()])
    world.add("/data/2024/acs/acs5", ok_body(bundle_table("100", "5")))
    result = _place(world)
    assert result["ok"] is True
    assert result["estimates_read"] is True
    assert result["reads_as_calendar_year"] is False
    assert result["vintage_year"] == 2024
    assert result["geography"]["name"] == "Austin city"
    assert result["geography"]["state_fips"] == "48"
    assert result["geography"]["place_fips"] == "05000"
    figure = result["figures"][0]
    assert figure["variable_id"] == "B01003_001E"
    assert figure["margin_variable_id"] == "B01003_001M"
    assert figure["estimate"] == 100
    assert figure["margin_of_error"] == 5
    assert figure["reads_as_calendar_year"] is False
    assert "vintage 2024" in figure["sentence"]
    assert world.keys == [SAMPLE]
    assert all("key" not in call["names"] for call in world.calls if call["host"] != "api.census.gov")


def test_one_incorporated_place_and_one_cdp_return_candidates_and_no_numbers():
    world = World()
    place_routes(world, [austin_row()], [{"NAME": "Austin CDP", "STATE": "48", "PLACE": "05001", "GEOID": "4805001"}])

    def reader():
        raise AssertionError("key read")

    result = make_service(world, reader=reader).place({"place": "Austin, TX", "year": 2024})
    assert result["ok"] is True
    assert result["resolved"] is False
    assert result["estimates_read"] is False
    assert result["match_count"] == 2
    assert "figures" not in result
    assert all(call["host"] == "tigerweb.geo.census.gov" for call in world.calls)


def test_many_places_include_cdp_rows_and_fetch_no_numbers():
    rows = []
    for index in range(16):
        rows.append({"NAME": "Springfield city", "STATE": str(index + 1).zfill(2), "PLACE": "12345", "GEOID": "x"})
    cdp = [{"NAME": "Springfield CDP", "STATE": "12", "PLACE": "11111", "GEOID": "1211111"}]
    world = World()
    place_routes(world, rows, cdp)
    result = make_service(world).place({"place": "Springfield", "year": 2024})
    assert result["ok"] is True
    assert result["resolved"] is False
    assert result["estimates_read"] is False
    assert result["match_count"] == 17
    assert result["sentence"] == "Several geographies matched that name, so no estimate was read. Pass one candidate name."
    summaries = [row["summary"] for row in result["candidates"]]
    assert "Census Designated Place" in summaries
    assert any("MapServer/5" in call["path"] for call in world.calls)
    assert world.keys == []


def test_zero_matches_do_not_invent_a_number():
    world = World()
    place_routes(world, [])
    result = make_service(world).place({"place": "NoSuchVille, TX", "year": 2024})
    assert result["resolved"] is False
    assert result["match_count"] == 0
    assert result["estimates_read"] is False
    assert result["candidates"] == []
    assert result["sentence"] == "No geography matched that name, so no estimate was read."
    blob = json.dumps(result)
    assert "979882" not in blob
    assert world.keys == []


def test_street_with_no_geocoder_match_does_not_fall_through_to_tiger():
    world = World()
    world.add("onelineaddress", ok_body({"result": {"addressMatches": []}}))
    result = make_service(world).place({"place": "4600 Silver Hill Rd, Washington, DC 20233", "year": 2024})
    assert result["match_count"] == 0
    assert result["estimates_read"] is False
    assert world.hosts() == ["geocoding.geo.census.gov"]


def test_street_match_reports_the_cdp_name():
    world = World()
    world.add("onelineaddress", ok_body({"result": {"addressMatches": [{
        "matchedAddress": "4600 SILVER HILL RD, SUITLAND, MD, 20746",
        "geographies": {
            "Incorporated Places": [],
            "Census Designated Places": [{
                "NAME": "Suitland CDP", "STATE": "24", "PLACE": "75725", "GEOID": "2475725",
            }],
            "Counties": [{"NAME": "Prince George's County", "STATE": "24", "COUNTY": "033"}],
        },
    }]}}))
    world.add("/data/2024/acs/acs5", ok_body(bundle_table("100", "5", name="Suitland CDP, Maryland")))
    result = with_key(world).place({"place": "4600 Silver Hill Rd, Washington, DC 20233", "year": 2024})
    assert result["geography"]["name"] == "Suitland CDP"
    assert result["geography"]["state_fips"] == "24"
    assert result["geography"]["county_fips"] == "033"
    assert result["name"] == "Suitland CDP, Maryland"
    assert "Washington" not in result["name"]
    assert "Washington" not in result["geography"]["name"]


def test_exact_state_names_do_not_call_tiger():
    world = World()
    found = plugin.geography.resolve_place("TX", world.transport)
    assert found.kind == "one"
    assert found.payload["state_fips"] == "48"
    assert world.calls == []
    lower = plugin.geography.resolve_place("tx", world.transport)
    assert lower.payload["state_fips"] == "48"
    dc = plugin.geography.resolve_place("Washington, DC", world.transport)
    assert dc.payload["state_fips"] == "11"
    district = plugin.geography.resolve_place("District of Columbia", world.transport)
    assert district.payload["state_fips"] == "11"
    state = plugin.geography.resolve_place("Washington State", world.transport)
    assert state.payload["state_fips"] == "53"
    wa = plugin.geography.resolve_place("WA", world.transport)
    assert wa.payload["state_fips"] == "53"


def test_bare_washington_keeps_the_state_when_the_place_list_is_capped():
    rows = []
    for index in range(21):
        rows.append({
            "NAME": "Washington city",
            "STATE": str((index % 50) + 1).zfill(2),
            "PLACE": "77777",
            "GEOID": "x",
        })
    world = World()
    place_routes(world, rows)
    found = plugin.geography.resolve_place("Washington", world.transport)
    assert found.kind == "candidates"
    summaries = [row["summary"] for row in found.payload["candidates"]]
    assert summaries[-1] == "State"
    assert len(found.payload["candidates"]) == plugin.hosts.CANDIDATE_CAP
    assert found.payload["truncated"] is True
    assert found.payload["match_count"] == 22
    assert found.payload["estimates_read"] is False


def test_bare_washington_is_candidates_including_the_state():
    world = World()
    place_routes(world, [
        {"NAME": "Washington city", "STATE": "17", "PLACE": "77777", "GEOID": "1777777"},
        {"NAME": "Washington city", "STATE": "18", "PLACE": "77777", "GEOID": "1877777"},
    ])
    found = plugin.geography.resolve_place("Washington", world.transport)
    assert found.kind == "candidates"
    summaries = [row["summary"] for row in found.payload["candidates"]]
    assert "State" in summaries
    assert found.payload["estimates_read"] is False
    assert any("MapServer/5" in call["path"] for call in world.calls)


def test_bare_state_names_that_are_also_places_stay_candidates():
    for folded in sorted(plugin.states.AMBIGUOUS_STATE_NAMES):
        world = World()
        place_routes(world, [])
        found = plugin.geography.resolve_place(folded, world.transport)
        assert found.kind == "candidates", folded
        assert found.payload["estimates_read"] is False
        assert found.payload["candidates"][-1]["summary"] == "State"
        assert found.payload["candidates"][-1]["name"].lower() == folded


def test_postal_abbreviation_and_state_suffix_skip_places():
    world = World()
    for folded in sorted(plugin.states.AMBIGUOUS_STATE_NAMES):
        abbr = plugin.states.BY_NAME[folded][0]
        found = plugin.geography.resolve_place(abbr, world.transport)
        assert found.kind == "one"
        assert found.payload["state_abbr"] == abbr
        named = plugin.geography.resolve_place(folded + " state", world.transport)
        assert named.kind == "one"
        assert named.payload["state_abbr"] == abbr
    assert world.calls == []
    place_routes(world, [])
    texas = plugin.geography.resolve_place("Texas", world.transport)
    assert texas.kind == "one"
    assert texas.payload["state_fips"] == "48"
    assert any("MapServer/4" in call["path"] for call in world.calls)
    assert any("MapServer/5" in call["path"] for call in world.calls)
    assert "UPPER(NAME) IN" in world.calls[-1]["query"]["where"][0]


def test_new_york_ny_selects_the_one_exact_name():
    world = World()
    place_routes(world, [
        {"NAME": "New York city", "STATE": "36", "PLACE": "51000", "GEOID": "3651000"},
        {"NAME": "New York Mills village", "STATE": "36", "PLACE": "50705", "GEOID": "3650705"},
    ])
    world.add("/data/2024/acs/acs5", ok_body(bundle_table("100", "5", name="New York city, New York")))
    result = with_key(world).place({"place": "New York, NY", "year": 2024})
    assert result["ok"] is True
    assert result["resolved"] is True
    assert result["estimates_read"] is True
    assert result["geography"]["name"] == "New York city"
    assert result["geography"]["place_fips"] == "51000"
    assert result["geography"]["state_fips"] == "36"
    assert "New York Mills" not in json.dumps(result["geography"])
    where = world.calls[0]["query"]["where"][0]
    assert where.startswith("UPPER(NAME) IN (")
    assert "'NEW YORK CITY'" in where
    assert "LIKE" not in where
    assert world.keys == [SAMPLE]
    official = World()
    place_routes(official, [
        {"NAME": "New York city", "STATE": "36", "PLACE": "51000", "GEOID": "3651000"},
    ])
    official.add("/data/2024/acs/acs5", ok_body(bundle_table("100", "5", name="New York city, New York")))
    chosen = with_key(official).place({"place": "New York City, NY", "year": 2024})
    assert chosen["resolved"] is True
    assert chosen["estimates_read"] is True
    assert chosen["geography"]["name"] == "New York city"
    assert chosen["geography"]["place_fips"] == "51000"
    where = official.calls[0]["query"]["where"][0]
    assert where.startswith("UPPER(NAME) IN (")
    assert "'NEW YORK CITY'" in where
    assert "'NEW YORK CITY CITY'" in where
    assert "LIKE" not in where
    lower = World()
    place_routes(lower, [
        {"NAME": "New York city", "STATE": "36", "PLACE": "51000", "GEOID": "3651000"},
    ])
    lower.add("/data/2024/acs/acs5", ok_body(bundle_table("100", "5", name="New York city, New York")))
    folded = with_key(lower).place({"place": "new york city, ny", "year": 2024})
    assert folded["resolved"] is True
    assert folded["geography"]["name"] == "New York city"
    assert lower.calls[0]["query"]["where"][0].startswith("UPPER(NAME) IN (")
    assert "'NEW YORK CITY'" in lower.calls[0]["query"]["where"][0]


def test_one_exact_county_is_selected_among_longer_names():
    world = World()
    world.add("State_County", ok_body(tiger([
        {"NAME": "King County", "STATE": "53", "COUNTY": "033", "GEOID": "53033"},
        {"NAME": "King and Queen County", "STATE": "51", "COUNTY": "097", "GEOID": "51097"},
    ])))
    found = plugin.geography.resolve_place("King County", world.transport)
    assert found.kind == "one"
    assert found.payload["name"] == "King County"
    assert found.payload["county_fips"] == "033"
    assert found.payload["state_fips"] == "53"


def test_two_exact_counties_stay_candidates():
    world = World()
    world.add("State_County", ok_body(tiger([
        {"NAME": "King County", "STATE": "53", "COUNTY": "033", "GEOID": "53033"},
        {"NAME": "King County", "STATE": "48", "COUNTY": "269", "GEOID": "48269"},
        {"NAME": "King and Queen County", "STATE": "51", "COUNTY": "097", "GEOID": "51097"},
    ])))
    found = plugin.geography.resolve_place("King County", world.transport)
    assert found.kind == "candidates"
    names = [row["name"] for row in found.payload["candidates"]]
    assert names.count("King County") == 2
    assert "King and Queen County" in names
    assert found.payload["estimates_read"] is False


def test_a_state_outside_the_known_list_stays_the_state_when_no_exact_place_exists():
    world = World()
    place_routes(world, [{"NAME": "Texas City city", "STATE": "48", "PLACE": "72392", "GEOID": "4872392"}])
    found = plugin.geography.resolve_place("Texas", world.transport)
    assert found.kind == "one"
    assert found.payload["name"] == "Texas"
    assert found.payload["state_fips"] == "48"


def test_an_exact_place_with_a_state_name_outside_the_known_list_stays_candidates():
    world = World()
    place_routes(world, [{"NAME": "Alabama city", "STATE": "28", "PLACE": "11111", "GEOID": "2811111"}])
    result = make_service(world).place({"place": "Alabama", "year": 2024})
    assert result["ok"] is True
    assert result["resolved"] is False
    assert result["estimates_read"] is False
    assert result["sentence"] == "Several geographies matched that name, so no estimate was read. Pass one candidate name."
    names = [row["name"] for row in result["candidates"]]
    assert "Alabama city" in names
    assert result["candidates"][-1]["summary"] == "State"
    assert world.keys == []


def test_a_failed_exact_place_check_does_not_choose_the_state():
    world = World()
    world.add("MapServer/4", HttpResult("http_status", status=503, body=b""))
    found = plugin.geography.resolve_place("Georgia", world.transport)
    assert found.kind == "error"
    assert found.payload["error_type"] == "http_status"
    assert "Georgia" not in json.dumps(found.payload.get("geography", ""))


def test_several_incorporated_places_on_an_address_keep_the_cdp():
    world = World()
    world.add("onelineaddress", ok_body({"result": {"addressMatches": [{
        "matchedAddress": "1 MAIN ST, SPRINGFIELD",
        "geographies": {
            "Incorporated Places": [
                {"NAME": "Springfield city", "STATE": "17", "PLACE": "72000", "GEOID": "1772000"},
                {"NAME": "Springfield city", "STATE": "25", "PLACE": "67000", "GEOID": "2567000"},
            ],
            "Census Designated Places": [
                {"NAME": "Springfield CDP", "STATE": "12", "PLACE": "11111", "GEOID": "1211111"},
            ],
        },
    }]}}))
    result = make_service(world).place({"place": "1 Main St, Springfield", "year": 2024})
    assert result["resolved"] is False
    assert result["estimates_read"] is False
    assert result["match_count"] == 3
    summaries = [row["summary"] for row in result["candidates"]]
    assert summaries.count("Incorporated Place") == 2
    assert "Census Designated Place" in summaries
    assert result["sentence"] == "Several geographies matched that name, so no estimate was read. Pass one candidate name."
    assert world.hosts() == ["geocoding.geo.census.gov"]


def test_a_truncated_place_list_does_not_pick_one_visible_exact_name():
    rows = [{"NAME": "York city", "STATE": "42", "PLACE": "87000", "GEOID": "4287000"}]
    for index in range(20):
        rows.append({
            "NAME": "Yorktown city",
            "STATE": "42",
            "PLACE": str(index + 1).zfill(5),
            "GEOID": "x",
        })
    world = World()
    place_routes(world, rows)
    found = plugin.geography.resolve_place("York, PA", world.transport)
    assert found.kind == "candidates"
    assert found.payload["truncated"] is True
    assert found.payload["estimates_read"] is False


def test_new_york_stays_a_candidate_list_and_reads_no_estimate():
    world = World()
    place_routes(world, [{"NAME": "New York city", "STATE": "36", "PLACE": "51000", "GEOID": "3651000"}])
    result = make_service(world).place({"place": "New York", "year": 2024})
    assert result["ok"] is True
    assert result["resolved"] is False
    assert result["estimates_read"] is False
    assert "geography" not in result
    names = [row["name"] for row in result["candidates"]]
    assert "New York city" in names
    assert "New York" in names
    compared = make_service(world).compare({"place_a": "New York", "place_b": "TX", "year": 2024})
    assert compared["ok"] is True
    assert compared["resolved"] is False
    assert compared["estimates_read"] is False
    assert compared["sentence"] == "No single geography was chosen, so no estimate was read."
    assert world.keys == []


def test_a_unique_longer_name_stays_a_candidate():
    world = World()
    place_routes(world, [{"NAME": "Midlothian city", "STATE": "48", "PLACE": "48096", "GEOID": "4848096"}])
    result = make_service(world).place({"place": "Midlo, TX", "year": 2024})
    assert result["resolved"] is False
    assert result["estimates_read"] is False
    assert result["match_count"] == 1
    assert result["candidates"][0]["name"] == "Midlothian city"
    assert result["sentence"] == "The only hit has a longer name, so no estimate was read. Pass the full place name."
    assert "figures" not in result
    assert world.keys == []


def test_a_unique_longer_county_name_stays_a_candidate():
    world = World()
    world.add("State_County", ok_body(tiger([{
        "NAME": "Prince Edward County", "STATE": "51", "COUNTY": "137", "GEOID": "51137",
    }])))
    found = plugin.geography.resolve_place("Prince E County, VA", world.transport)
    assert found.kind == "candidates"
    assert found.payload["name_differs"] is True
    assert found.payload["estimates_read"] is False
    assert found.payload["candidates"][0]["name"] == "Prince Edward County"


def test_county_uses_the_county_layer():
    world = World()
    world.add("State_County", ok_body(tiger([{
        "NAME": "King County", "STATE": "53", "COUNTY": "033", "GEOID": "53033",
    }])))
    found = plugin.geography.resolve_place("King County, WA", world.transport)
    assert found.kind == "one"
    assert found.payload["for"] == "county:033"
    assert found.payload["in"] == "state:53"
    assert world.hosts() == ["tigerweb.geo.census.gov"]


def test_place_characters_outside_the_allowlist_make_no_request():
    world = World()
    for text in ("Austin\nTX", "Austin; TX", "100%", "Austin_city", "Austin\\TX", "A" * 181):
        found = plugin.geography.resolve_place(text, world.transport)
        assert found.kind == "error"
        assert found.payload["ok"] is False
    assert world.calls == []


def test_unicode_and_apostrophe_are_quoted_into_the_where_clause():
    world = World()
    place_routes(world, [austin_row()])
    plugin.geography.resolve_place("San José, TX", world.transport)
    plugin.geography.resolve_place("O'Fallon, MO", world.transport)
    blob = "\n".join(world.plain)
    assert "SAN JOSÉ" in blob
    assert "O''FALLON" in blob


def test_lookup_forms_try_the_original_spelling_first():
    assert plugin.geography.LOOKUP_FORMS == ("original", "folded")
    assert plugin.geography._lookup_forms("San José") == ["San José", "San Jose"]
    assert plugin.geography._lookup_forms("San Jose") == ["San Jose"]
    assert plugin.geography._lookup_forms("Cañon") == ["Cañon", "Canon"]
    assert plugin.geography.fold_marks("Doña Ana") == "Dona Ana"
    assert plugin.geography.fold_marks("Española") == "Espanola"


def _where(url):
    return urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["where"][0]


def test_accented_san_jose_reaches_san_jose_city():
    seen = []

    def transport(url, cap="geo", timeout=20):
        where = _where(url)
        seen.append(where)
        if "JOSÉ" in where:
            return ok_body(tiger([]))
        if "JOSE" in where:
            if "MapServer/4" in url:
                return ok_body(tiger([{
                    "NAME": "San Jose city",
                    "STATE": "06",
                    "PLACE": "68000",
                    "GEOID": "0668000",
                }]))
            return ok_body(tiger([]))
        raise AssertionError(where)

    found = plugin.geography.resolve_place("San José, CA", transport)
    assert found.kind == "one"
    assert found.payload["name"] == "San Jose city"
    assert found.payload["state_fips"] == "06"
    assert found.payload["place_fips"] == "68000"
    assert any("JOSÉ" in where for where in seen)
    assert any("JOSE" in where and "JOSÉ" not in where for where in seen)


def test_accented_official_name_is_kept():
    seen = []

    def transport(url, cap="geo", timeout=20):
        where = _where(url)
        seen.append(where)
        if "Ñ" not in where:
            raise AssertionError(where)
        if "MapServer/4" in url:
            return ok_body(tiger([{
                "NAME": "Española city",
                "STATE": "35",
                "PLACE": "25170",
                "GEOID": "3525170",
            }]))
        return ok_body(tiger([]))

    found = plugin.geography.resolve_place("Española, NM", transport)
    assert found.kind == "one"
    assert found.payload["name"] == "Española city"
    assert found.payload["state_fips"] == "35"
    assert found.payload["place_fips"] == "25170"
    assert seen


def test_accented_county_name_is_kept():
    seen = []

    def transport(url, cap="geo", timeout=20):
        where = _where(url)
        seen.append(where)
        if "Ñ" not in where:
            raise AssertionError(where)
        return ok_body(tiger([{
            "NAME": "Doña Ana County",
            "STATE": "35",
            "COUNTY": "013",
            "GEOID": "35013",
        }]))

    found = plugin.geography.resolve_place("Doña Ana County, NM", transport)
    assert found.kind == "one"
    assert found.payload["name"] == "Doña Ana County"
    assert found.payload["summary"] == "County"
    assert found.payload["state_fips"] == "35"
    assert found.payload["county_fips"] == "013"
    assert len(seen) == 1


def test_missing_key_keeps_geography_and_skips_the_data_endpoint():
    world = World()
    place_routes(world, [austin_row()])
    result = make_service(world).place({"place": "Austin, TX", "year": 2024})
    assert result["ok"] is False
    assert result["error_type"] == "missing_key"
    assert result["estimates_read"] is False
    assert result["geography"]["name"] == "Austin city"
    assert result["reads_as_calendar_year"] is False
    assert any(item["variable_id"] == "B01003_001E" for item in result["variables"])
    assert plugin.errors.KEY_URL in result["error"]
    assert "<html" not in result["error"]
    assert all(call["host"] != "api.census.gov" for call in world.calls)


def test_missing_key_html_is_one_sentence():
    world = World()
    place_routes(world, [austin_row()])
    world.add("/data/2024/acs/acs5", HttpResult("missing_key_html", status=200, body=b""))
    result = with_key(world).place({"place": "Austin, TX", "year": 2024})
    assert result["error_type"] == "missing_key"
    assert result["estimates_read"] is False
    assert plugin.errors.KEY_URL in result["error"]
    assert "<html" not in json.dumps(result)
    assert SAMPLE not in json.dumps(result)


def test_sentinels_are_annotations_and_an_unknown_negative_can_pass():
    world = World()
    world.add("/data/2024/acs/acs5", ok_body(one_table("-666666666", "-555555555")))
    result = with_key(world).get({
        "state_fips": "48",
        "variables": ["B01003_001E"],
        "year": 2024,
    })
    figure = result["figures"][0]
    assert figure["estimate"] is None
    assert figure["annotation"] == "-"
    assert figure["margin_of_error"] is None
    assert figure["moe_annotation"] == "*****"
    blob = json.dumps(result)
    assert "-666666666" not in blob
    assert "-555555555" not in blob
    assert figure["margin_of_error"] != 0

    world2 = World()
    world2.add("/data/2024/acs/acs5", ok_body(one_table("-1", "2")))
    other = with_key(world2).get({
        "state_fips": "48",
        "variables": ["B01003_001E"],
        "year": 2024,
    })
    assert other["figures"][0]["estimate"] == -1
    assert other["figures"][0]["margin_of_error"] == 2


def test_null_is_not_zero_and_median_annotation_stays_text():
    parsed = plugin.sentinels.parse_cell(None)
    assert parsed["value"] is None
    assert parsed["value"] != 0
    assert "No data" in parsed["note"]
    word = plugin.sentinels.parse_cell("median-")
    assert word["value"] is None
    assert word["annotation"] == "median-"
    empty = plugin.sentinels.parse_cell("")
    assert empty["value"] is None


def test_http_204_empty_data_is_an_empty_geography():
    """HTTP 204 with an empty body keeps the geography and does not say to retry."""
    one = World()
    one.add("State_County", ok_body(tiger([
        {"NAME": "Loving County", "STATE": "48", "COUNTY": "301", "GEOID": "48301"},
    ])))
    one.add("/data/2024/acs/acs1", HttpResult("empty_table", status=204, body=b""))
    acs1 = with_key(one).get({
        "state_fips": "48",
        "county_fips": "301",
        "variables": ["B01003_001E"],
        "year": 2024,
        "dataset": "acs/acs1",
    })
    assert acs1["ok"] is True
    assert acs1["empty_row"] is True
    assert acs1["estimates_read"] is False
    assert acs1["vintage_year"] == 2024
    assert acs1["dataset"] == "acs/acs1"
    assert acs1["figures"] == []
    assert acs1["geography"]["state_fips"] == "48"
    assert acs1["geography"]["county_fips"] == "301"
    assert "No county figure was substituted." in acs1["sentence"]
    assert "Retry later" not in acs1["sentence"]
    assert "33" not in json.dumps(acs1)

    five = World()
    five.add("State_County", ok_body(tiger([])))
    five.add("/data/2024/acs/acs5", HttpResult("empty_table", status=204, body=b""))
    acs5 = with_key(five).get({
        "state_fips": "48",
        "county_fips": "999",
        "variables": ["B01003_001E"],
        "year": 2024,
    })
    assert acs5["ok"] is True
    assert acs5["empty_row"] is False
    assert acs5["resolved"] is False
    assert acs5["estimates_read"] is False
    assert acs5["roster"] == "absent"
    assert acs5["geography"]["county_fips"] == "999"
    assert "absent from the Census roster" in acs5["sentence"]
    assert "999" in acs5["sentence"]
    assert "HTTP 204" not in acs5["sentence"]
    assert "Retry later" not in acs5["sentence"]
    assert "33" not in json.dumps(acs5)


def test_compare_keeps_a_204_empty_geography():
    def transport(url, cap="data", timeout=20):
        text = urllib.parse.unquote(url.split("key=", 1)[0])
        if "state:06" in text:
            return HttpResult("empty_table", status=204, body=b"")
        return ok_body(one_table("10", "1"))

    svc = plugin.service.Census(transport=transport, key_reader=lambda: (SAMPLE, None), clock=lambda: CLOCK)
    result = svc.compare({"place_a": "TX", "place_b": "CA", "year": 2024, "dataset": "acs/acs1"})
    assert result["ok"] is True
    assert result["estimates_read"] is False
    assert result["place_a"]["estimates_read"] is True
    assert result["place_b"]["empty_row"] is True
    assert result["place_b"]["geography"]["state_fips"] == "06"
    assert "Retry later" not in json.dumps(result)
    assert "No county figure was substituted." in result["place_b"]["sentence"]


def test_header_only_and_acs1_do_not_substitute_a_county():
    header = ["NAME", "B01003_001E", "B01003_001M", "B01003_001EA", "B01003_001MA"]
    world = World()
    world.add("/data/2024/acs/acs1", ok_body([header]))
    result = with_key(world).get({
        "state_fips": "48",
        "place_fips": "05000",
        "variables": ["B01003_001E"],
        "year": 2024,
        "dataset": "acs/acs1",
    })
    assert result["ok"] is True
    assert result["empty_row"] is True
    assert result["estimates_read"] is False
    assert result["figures"] == []
    assert "No county figure was substituted." in result["sentence"]


def test_two_rows_are_not_averaged_and_a_wrong_shape_is_not_a_sheet():
    header = ["NAME", "B01003_001E", "B01003_001M", "B01003_001EA", "B01003_001MA"]
    world = World()
    world.add("/data/2024/acs/acs5", ok_body([header, ["A", "424242", "1", None, None], ["B", "7", "1", None, None]]))
    result = with_key(world).get({"state_fips": "48", "variables": ["B01003_001E"], "year": 2024})
    assert result["ok"] is False
    assert result["error_type"] == "unexpected_body"
    assert "424242" not in json.dumps(result)

    world2 = World()
    world2.add("/data/2024/acs/acs5", ok_body({"data": [{"population": 1}]}))
    bad = with_key(world2).get({"state_fips": "48", "variables": ["B01003_001E"], "year": 2024})
    assert bad["error_type"] == "unexpected_body"
    assert "population" not in json.dumps(bad)


def test_allowlist_stops_before_http():
    world = World()
    svc = with_key(world)
    cases = [
        svc.get({"state_fips": "48", "place_fips": "*", "variables": ["B01003_001E"], "year": 2024}),
        svc.get({"state_fips": "*", "variables": ["B01003_001E"], "year": 2024}),
        svc.get({"state_fips": "48", "county_fips": "453", "place_fips": "05000", "variables": ["B01003_001E"], "year": 2024}),
        svc.get({"state_fips": "48", "variables": ["b01003_001e"], "year": 2024}),
        svc.get({"state_fips": "48", "variables": ["B01003_001E"] * 11, "year": 2024}),
        svc.get({"state_fips": "48", "variables": ["B01003_001E"], "year": 2024, "url": "https://evil.example"}),
        svc.get({"state_fips": "48", "variables": ["B01003_001E"], "year": "latest"}),
        svc.get({"state_fips": "48", "variables": ["B01003_001E"], "year": True}),
        svc.get({"state_fips": "48", "variables": ["B01003_001E"], "year": 2027}),
        svc.get({"state_fips": 48, "variables": ["B01003_001E"], "year": 2024}),
        svc.place({"place": "Austin, TX", "dataset": "pep/charage", "year": 2024}),
        svc.datasets({"limit": True}),
        svc.variables({"query": "population", "dataset": "dec/sf1", "year": 2024}),
    ]
    assert all(item["ok"] is False and item["error_type"] == "bad_args" and item["next_step"] for item in cases)
    assert world.calls == []


def test_digit_year_is_accepted_and_equal_compare_years_are_that_year():
    world = World()
    world.add("/data/2024/acs/acs5", ok_body(one_table("10", "1")))
    result = with_key(world).get({
        "state_fips": "48",
        "variables": ["B01003_001E"],
        "year": "2024",
    })
    assert result["vintage_year"] == 2024
    assert result["reads_as_calendar_year"] is False
    assert all("2026" not in call["path"] for call in world.calls)

    seen = []

    def transport(url, cap="data", timeout=20):
        text = urllib.parse.unquote(url.split("key=", 1)[0])
        seen.append("48" if "state:48" in text else "06" if "state:06" in text else "other")
        return ok_body(one_table("10", "1"))

    svc = plugin.service.Census(
        transport=transport,
        key_reader=lambda: (SAMPLE, None),
        clock=lambda: CLOCK,
    )
    compared = svc.compare({"place_a": "TX", "place_b": "CA", "year_a": 2024, "year_b": 2024})
    assert compared["ok"] is True
    assert compared["same_year"] is True
    assert compared["vintage_year"] == 2024
    assert compared["reads_as_calendar_year"] is False
    assert seen == ["48", "06"]


def test_year_mismatch_and_a_bad_year_send_nothing():
    world = World()
    svc = with_key(world)
    mismatch = svc.compare({"place_a": "TX", "place_b": "CA", "year_a": 2024, "year_b": 2023})
    assert mismatch["error_type"] == "year_mismatch"
    bad = svc.compare({"place_a": "TX", "place_b": "CA", "year_a": "soon", "year_b": 2024})
    assert bad["error_type"] == "bad_args"
    assert world.calls == []


def test_compare_does_not_return_one_side():
    def transport(url, cap="data", timeout=20):
        text = urllib.parse.unquote(url.split("key=", 1)[0])
        if "state:06" in text:
            return http_status(500)
        if "state:48" in text:
            return ok_body(one_table("10", "1"))
        raise AssertionError("unmapped path")

    svc = plugin.service.Census(transport=transport, key_reader=lambda: (SAMPLE, None), clock=lambda: CLOCK)
    result = svc.compare({"place_a": "TX", "place_b": "CA", "year": 2024})
    assert result["ok"] is False
    assert "figures" not in result
    assert "place_a" not in result

    calls = {"n": 0}

    def first_fails(url, cap="data", timeout=20):
        calls["n"] += 1
        return http_status(500)

    svc2 = plugin.service.Census(transport=first_fails, key_reader=lambda: (SAMPLE, None), clock=lambda: CLOCK)
    failed = svc2.compare({"place_a": "TX", "place_b": "CA", "year": 2024})
    assert failed["ok"] is False
    assert calls["n"] == 1


def test_ambiguous_compare_fetches_no_estimates():
    rows = []
    for index in range(3):
        rows.append({"NAME": "Springfield city", "STATE": str(index + 1).zfill(2), "PLACE": "12345", "GEOID": "x"})
    world = World()
    place_routes(world, rows)
    result = with_key(world).compare({"place_a": "Springfield", "place_b": "TX", "year": 2024})
    assert result["estimates_read"] is False
    assert result["resolved"] is False
    assert result["sentence"] == "No single geography was chosen, so no estimate was read."
    assert world.keys == []


def test_a_left_side_error_does_not_resolve_the_right_side():
    world = World()
    world.add("MapServer/4", HttpResult("timeout"))
    result = with_key(world).compare({"place_a": "Austin, TX", "place_b": "TX", "year": 2024})
    assert result["ok"] is False
    assert result["error_type"] == "timeout"
    assert len(world.calls) == 1


def test_discover_labels_the_listed_vintage_and_a_5xx_does_not_walk_down():
    world = World()
    world.add("/data/2026/acs/acs5.json", http_status(404))
    world.add("/data/2025/acs/acs5.json", http_status(404))
    world.add("/data/2024/acs/acs5.json", ok_body(descriptor(2024)))
    world.add("geography.json", ok_body({"fips": [{"name": "state", "geoLevelDisplay": "040"}]}))
    result = make_service(world).geographies({"dataset": "acs/acs5"})
    assert result["ok"] is True
    assert result["vintage_year"] == 2024
    assert result["reads_as_calendar_year"] is False
    assert result["estimates_read"] is False
    assert result["total"] == 1

    failed = World()
    failed.add("/data/2026/acs/acs5.json", http_status(500))
    failed.add("/data/2024/acs/acs5.json", ok_body(descriptor(2024)))
    bad = make_service(failed).geographies({})
    assert bad["error_type"] == "vintage_unreadable"
    assert bad["estimates_read"] is False
    assert len(failed.calls) == 1


def test_unavailable_descriptor_is_not_called_the_newest():
    world = World()
    world.add("/data/2026/acs/acs5.json", ok_body(descriptor(2026, available=False)))
    world.add("/data/2025/acs/acs5.json", http_status(404))
    world.add("/data/2024/acs/acs5.json", ok_body(descriptor(2024)))
    world.add("variables/B01003_001E.json", ok_body(variable_doc()))

    def reader():
        raise AssertionError("key read")

    result = make_service(world, reader=reader).variables({"code": "B01003_001E"})
    assert result["vintage_year"] == 2024
    assert result["variable_id"] == "B01003_001E"
    assert result["estimates_read"] is False
    assert any(call["path"].endswith("/2024/acs/acs5/variables/B01003_001E.json") for call in world.calls)
    assert all(not call["path"].endswith("/variables.json") for call in world.calls)


def test_explicit_variable_lookup_is_one_keyless_get():
    world = World()
    world.add("variables/B01003_001E.json", ok_body(variable_doc()))

    def reader():
        raise AssertionError("key read")

    result = make_service(world, reader=reader).variables({
        "code": "B01003_001E",
        "year": 2023,
        "dataset": "acs/acs5",
    })
    assert result["ok"] is True
    assert result["label"] == "Estimate!!Total"
    assert result["vintage_year"] == 2023
    assert len(world.calls) == 1
    assert world.calls[0]["host"] == "api.census.gov"
    assert "key" not in world.calls[0]["names"]


def test_keyword_search_reports_the_cap_and_skips_name():
    groups = {"groups": [
        {"name": "B19013", "description": "Median Household Income"},
        {"name": "B19001", "description": "Household Income"},
        {"name": "B19113", "description": "Median Family Income"},
        {"name": "B19202", "description": "Median Nonfamily Income"},
        {"name": "B01003", "description": "Total Population"},
    ]}

    def group_file(name):
        return {"variables": {
            "NAME": {"label": "Geographic Area Name", "concept": "Name"},
            name + "_001E": {"label": "Estimate!!Median income", "concept": "Income"},
        }}

    def transport(url, cap="data", timeout=20):
        if url.endswith("/groups.json"):
            return ok_body(groups)
        if "/groups/" in url:
            name = url.rstrip("/").rsplit("/", 1)[-1].replace(".json", "")
            return ok_body(group_file(name))
        raise AssertionError("unmapped path")

    svc = plugin.service.Census(transport=transport, key_reader=lambda: (None, "missing"), clock=lambda: CLOCK)
    result = svc.variables({"query": "income", "year": 2024})
    assert result["groups_matched"] == 4
    assert result["groups_opened"] == 3
    assert result["search_capped"] is True
    assert result["complete"] is False
    assert "3 of 4" in result["sentence"]
    assert all(item["variable_id"] != "NAME" for item in result["variables"])
    assert result["estimates_read"] is False


def test_keyword_with_no_group_name_is_not_a_complete_absence():
    groups = {"groups": [
        {"name": "B01003", "description": "Total Population"},
        {"name": "B25001", "description": "Housing Units"},
    ]}

    def transport(url, cap="data", timeout=20):
        if url.endswith("/groups.json"):
            return ok_body(groups)
        raise AssertionError("group file opened")

    svc = plugin.service.Census(transport=transport, key_reader=lambda: (None, "missing"), clock=lambda: CLOCK)
    result = svc.variables({"query": "zzz-no-such-topic", "year": 2024})
    assert result["ok"] is True
    assert result["complete"] is False
    assert result["groups_matched"] == 0
    assert result["groups_opened"] == 0
    assert result["variables"] == []
    assert result["sentence"] == (
        "The search read group names and descriptions. "
        "No group name or description matched, so variable text was not read."
    )
    assert result["estimates_read"] is False


def test_a_capped_search_that_fails_midway_is_not_an_empty_catalog():
    groups = {"groups": [
        {"name": "B19013", "description": "Household Income"},
        {"name": "B19001", "description": "Household Income bands"},
    ]}
    opened = {"n": 0}

    def transport(url, cap="data", timeout=20):
        if url.endswith("/groups.json"):
            return ok_body(groups)
        opened["n"] += 1
        if opened["n"] == 2:
            return HttpResult("timeout")
        return ok_body({"variables": {"B19013_001E": {"label": "Estimate!!Income", "concept": "Income"}}})

    svc = plugin.service.Census(transport=transport, key_reader=lambda: (None, "missing"), clock=lambda: CLOCK)
    result = svc.variables({"query": "Income", "year": 2024})
    assert result["ok"] is False
    assert result["complete"] is False
    assert result["groups_matched"] == 2
    assert result["groups_opened"] == 1
    assert "variables" not in result or result.get("variables") in (None, [])


def test_catalog_truncation_and_a_missing_array_are_not_zero_datasets():
    world = World()
    world.add("/data.json", HttpResult("ok", 200, b'{"dataset":[]}', "application/json", True))
    result = make_service(world).datasets({})
    assert result["ok"] is False
    assert result["error_type"] == "incomplete"
    assert result["complete"] is False
    assert "total" not in result

    missing = World()
    missing.add("/data.json", ok_body({"@type": "dcat:Catalog"}))
    bad = make_service(missing).datasets({})
    assert bad["error_type"] == "unexpected_body"
    assert "total" not in bad

    page = World()
    page.add("/data.json", ok_body({"dataset": [
        {"c_vintage": 1999, "c_dataset": ["nonemp"], "title": "Nonemployer", "c_isAvailable": True},
        {"c_vintage": 2024, "c_dataset": ["acs", "acs5"], "title": "ACS 5-Year", "c_isAvailable": True},
        {"c_vintage": 2024, "c_dataset": ["acs", "acs1"], "title": "ACS 1-Year", "c_isAvailable": True},
    ]}))
    listed = make_service(page).datasets({"query": "", "limit": 1})
    assert listed["total"] == 3
    assert listed["returned"] == 1
    assert listed["datasets"][0]["dataset"] == "nonemp"
    assert listed["truncated"] is True
    assert listed["complete"] is False
    filtered = make_service(page).datasets({})
    assert filtered["total"] == 2
    assert all(item["dataset"].startswith("acs/") for item in filtered["datasets"])
    assert all(item["reads_as_calendar_year"] is False for item in filtered["datasets"])


def test_group_id_opens_one_file_and_a_missing_variables_object_is_not_empty():
    world = World()
    world.add("/groups/B19013.json", ok_body({"variables": {
        "NAME": {"label": "Name"},
        "B19013_001E": {"label": "Estimate!!Median household income", "concept": "Income"},
    }}))
    result = make_service(world).variables({"query": "B19013", "year": 2024})
    assert result["groups_matched"] == 1
    assert result["groups_opened"] == 1
    assert [item["variable_id"] for item in result["variables"]] == ["B19013_001E"]

    broken = World()
    broken.add("/groups/B19013.json", ok_body({"title": "income"}))
    bad = make_service(broken).variables({"query": "B19013", "year": 2024})
    assert bad["error_type"] == "unexpected_body"
    assert "total" not in bad


def test_unscoped_and_bad_keys_send_no_data_request():
    world = World()
    place_routes(world, [austin_row()])
    unscoped = make_service(world, reader=lambda: (None, "unscoped")).place({"place": "Austin, TX", "year": 2024})
    assert unscoped["error_type"] == "secret_unscoped"
    assert all(call["host"] != "api.census.gov" for call in world.calls)

    world2 = World()
    place_routes(world2, [austin_row()])
    bad = make_service(world2, reader=lambda: (None, "bad")).place({"place": "Austin, TX", "year": 2024})
    assert bad["error_type"] == "bad_args"
    assert "was not sent" in bad["next_step"]
    assert world2.keys == []


def test_handler_json_hides_exception_text():
    def boom(_url, cap="data", timeout=20):
        raise RuntimeError(SAMPLE + " boom")

    plugin.tools.SERVICE = plugin.service.Census(
        transport=boom,
        key_reader=lambda: (SAMPLE, None),
        clock=lambda: CLOCK,
    )
    try:
        raw = plugin.tools.HANDLERS["census_get"]({
            "state_fips": "48",
            "variables": ["B01003_001E"],
            "year": 2024,
        })
    finally:
        plugin.tools.SERVICE = plugin.service.Census()
    assert SAMPLE not in raw
    assert "boom" not in raw
    payload = json.loads(raw)
    assert payload["error_type"] == "internal"
    assert payload["ok"] is False


def test_failure_kinds_keep_a_next_step():
    kinds = (
        ("timeout", "timeout"),
        ("connection_failed", "connection_failed"),
        ("redirect", "redirect"),
        ("host_refused", "host_refused"),
        ("html", "unexpected_body"),
    )
    for kind, expected in kinds:
        world = World()
        world.add("/data.json", HttpResult(kind, status=200, body=b""))
        result = make_service(world).datasets({})
        assert result["error_type"] == expected
        assert result["ok"] is False
        assert result["next_step"]
        assert result["estimates_read"] is False
        assert result["complete"] is False


def test_budget_stops_before_a_partial_list(monkeypatch):
    monkeypatch.setattr(plugin.service, "MAX_CALLS", 0)
    world = World()
    world.add("/data.json", ok_body({"dataset": []}))
    result = make_service(world).datasets({})
    assert result["error_type"] == "budget"
    assert result["estimates_read"] is False
    assert world.calls == []


def test_environ_and_a_child_dotenv_are_not_the_key(monkeypatch, tmp_path):
    monkeypatch.setenv("CENSUS_API_KEY", SAMPLE)
    (tmp_path / ".env").write_text("CENSUS_API_KEY=" + SAMPLE + "\nEXTRA_CENSUS_MARKER=1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("EXTRA_CENSUS_MARKER", raising=False)
    value, problem = plugin.service.read_api_key()
    assert value is None
    assert problem == "missing"
    assert "EXTRA_CENSUS_MARKER" not in __import__("os").environ


def test_import_failure_is_the_missing_key_sentence(monkeypatch):
    import builtins
    real = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name == "agent.secret_scope":
            raise ImportError("blocked")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    assert plugin.service.read_api_key() == (None, "missing")


def test_unscoped_secret_error_sends_nothing(monkeypatch):
    pytest = __import__("pytest")
    scope = pytest.importorskip("agent.secret_scope")

    def boom(*_args, **_kwargs):
        raise scope.UnscopedSecretError("no")

    monkeypatch.setattr(scope, "get_secret", boom)
    assert plugin.service.read_api_key()[1] == "unscoped"


def test_non_ascii_key_is_not_returned(monkeypatch):
    pytest = __import__("pytest")
    scope = pytest.importorskip("agent.secret_scope")
    monkeypatch.setattr(scope, "get_secret", lambda *_args, **_kwargs: "caf\u00e9-key")
    value, problem = plugin.service.read_api_key()
    assert value is None
    assert problem == "bad"
    world = World()
    place_routes(world, [austin_row()])
    result = make_service(world, reader=plugin.service.read_api_key).place({"place": "Austin, TX", "year": 2024})
    assert result["error_type"] == "bad_args"
    assert "caf" not in json.dumps(result)
    assert world.keys == []


def _fields(call):
    return call["query"]["outFields"][0].split(",")


def test_each_layer_asks_only_for_columns_that_layer_has():
    world = World()
    place_routes(world, [austin_row()])
    found = plugin.geography.resolve_place("Austin, TX", world.transport)
    assert found.kind == "one"
    place_calls = [call for call in world.calls if "MapServer/4" in call["path"] or "MapServer/5" in call["path"]]
    assert place_calls
    for call in place_calls:
        fields = _fields(call)
        assert "COUNTY" not in fields
        assert "PLACE" in fields
        assert call["query"]["resultRecordCount"] == ["21"]

    counties = World()
    counties.add("State_County", ok_body(tiger([{
        "NAME": "King County", "STATE": "53", "COUNTY": "033", "GEOID": "53033",
    }])))
    county = plugin.geography.resolve_place("King County, WA", counties.transport)
    assert county.kind == "one"
    fields = _fields(counties.calls[0])
    assert "PLACE" not in fields
    assert "COUNTY" in fields


def test_a_tiger_error_object_does_not_invent_a_number_or_say_retry():
    world = World()
    world.add("MapServer/4", ok_body({"error": {"code": 400, "message": "Failed to execute query.", "details": []}}))
    found = plugin.geography.resolve_place("Austin, TX", world.transport)
    assert found.kind == "error"
    assert found.payload["error_type"] == "unexpected_body"
    assert "Retry later" not in found.payload["next_step"]
    blob = json.dumps(found.payload)
    assert "05000" not in blob
    assert "Austin city" not in blob


def test_all_null_cells_are_an_empty_row_for_both_products():
    header = ["NAME", "B01003_001E", "B01003_001M", "B01003_001EA", "B01003_001MA"]
    row = ["Austin city, Texas", None, None, None, None]
    world = World()
    world.add("/data/2024/acs/acs5", ok_body([header, row]))
    five = with_key(world).get({
        "state_fips": "48",
        "place_fips": "05000",
        "variables": ["B01003_001E"],
        "year": 2024,
    })
    assert five["ok"] is True
    assert five["empty_row"] is True
    assert five["estimates_read"] is False
    assert "Each figure carries" not in five["sentence"]
    assert five["figures"][0]["estimate"] is None
    assert five["figures"][0]["estimate"] != 0

    world1 = World()
    world1.add("/data/2024/acs/acs1", ok_body([header, row]))
    one = with_key(world1).get({
        "state_fips": "48",
        "place_fips": "05000",
        "variables": ["B01003_001E"],
        "year": 2024,
        "dataset": "acs/acs1",
    })
    assert one["empty_row"] is True
    assert one["estimates_read"] is False
    assert "No county figure was substituted." in one["sentence"]
    assert "Each figure carries" not in one["sentence"]


def test_compare_of_empty_rows_does_not_claim_the_estimates_were_read():
    columns = plugin.acs.bundle_columns()
    empty = [None] * len(columns)
    empty[0] = "Place"
    table = [columns, empty]

    def transport(url, cap="data", timeout=20):
        return ok_body(table)

    svc = plugin.service.Census(transport=transport, key_reader=lambda: (SAMPLE, None), clock=lambda: CLOCK)
    result = svc.compare({"place_a": "TX", "place_b": "CA", "year": 2024})
    assert result["ok"] is True
    assert result["estimates_read"] is False
    assert "Each figure carries" not in result["place_a"]["sentence"]
    assert "Each figure carries" not in result["place_b"]["sentence"]


def test_compare_keeps_a_404_next_step_on_the_second_place():
    def transport(url, cap="data", timeout=20):
        text = urllib.parse.unquote(url.split("key=", 1)[0])
        if "state:06" in text:
            return http_status(404)
        return ok_body(one_table("10", "1"))

    svc = plugin.service.Census(transport=transport, key_reader=lambda: (SAMPLE, None), clock=lambda: CLOCK)
    result = svc.compare({"place_a": "TX", "place_b": "CA", "year": 2024})
    assert result["ok"] is False
    assert result["error_type"] == "http_status"
    assert "Retry later" not in result["next_step"]
    assert "place_a" not in result


def test_invalid_key_redirect_on_a_data_call_names_the_key():
    world = World()
    world.add("/data/2024/acs/acs5", HttpResult("invalid_key_redirect", status=0, body=b""))
    result = with_key(world).get({
        "state_fips": "48",
        "variables": ["B01003_001E"],
        "year": 2024,
    })
    assert result["error_type"] == "invalid_key"
    assert result["estimates_read"] is False
    assert "Retry later" not in result["next_step"]
    assert plugin.errors.KEY_URL in result["error"]
    assert SAMPLE not in json.dumps(result)
    assert "<html" not in json.dumps(result)


def test_place_input_kinds_match_the_table():
    """Every INPUT_KINDS row, including each state name followed by State."""
    samples = {
        "address": "4600 Silver Hill Rd, Washington, DC 20233",
        "dc_alias": "Washington, DC",
        "state_suffix": "Texas State",
        "ambiguous_bare": "New York",
        "postal": "TX",
        "bare_state": "Georgia",
        "county": "King County, WA",
        "official_place": "Spring CDP, TX",
        "stem_place": "Spring, TX",
    }
    assert set(samples) == set(plugin.geography.INPUT_KINDS)
    clean = plugin.geography._clean
    classify = plugin.geography.classify_place
    for kind, text in samples.items():
        assert classify(clean(text)) == kind
    extras = (
        ("North Carolina State", "state_suffix"),
        ("Georgia State", "state_suffix"),
        ("New York State", "state_suffix"),
        ("Washington State", "state_suffix"),
        ("District of Columbia State", "state_suffix"),
        ("Puerto Rico State", "state_suffix"),
        ("texas state", "state_suffix"),
        ("North Carolina  State", "state_suffix"),
        ("Washington", "ambiguous_bare"),
        ("tx", "postal"),
        ("PR", "postal"),
        ("North Carolina", "bare_state"),
        ("New York city, NY", "official_place"),
        ("New York City, NY", "official_place"),
        ("Austin, TX", "stem_place"),
        ("Aust, TX", "stem_place"),
        ("Midlo, TX", "stem_place"),
        ("Nopeville, TX", "stem_place"),
        ("Springfield", "stem_place"),
        ("DC", "dc_alias"),
        ("District of Columbia", "dc_alias"),
    )
    for text, kind in extras:
        assert classify(clean(text)) == kind, text
    assert plugin.geography.CLEAN_STEPS == (
        "nfkc_strip",
        "trail_comment",
        "one_semicolon",
        "collapse_space",
    )
    cleaned = (
        ("Austin, TX;", "Austin, TX"),
        ("Austin, TX; # ignore", "Austin, TX"),
        ("Austin, TX // ignore", "Austin, TX"),
        ("  austin, tx  ", "austin, tx"),
        ("Ａｕｓｔｉｎ", "Austin"),
        ("San José, CA", "San José, CA"),
        ("Austin; TX", "Austin; TX"),
        ("Austin, TX;;", "Austin, TX"),
        (";;;", ""),
    )
    for raw, expected in cleaned:
        assert clean(raw) == expected, raw


def test_every_state_name_followed_by_state_skips_places():
    world = World()
    place_routes(world, [{
        "NAME": "Texas State city", "STATE": "48", "PLACE": "99999", "GEOID": "4899999",
    }])
    for abbr, fips, name in plugin.states.STATES:
        found = plugin.geography.resolve_place(name + " State", world.transport)
        assert found.kind == "one", name
        assert found.payload["summary"] == "State"
        assert found.payload["state_fips"] == fips
        assert found.payload["state_abbr"] == abbr
        assert found.payload["place_fips"] is None
        assert found.payload["for"] == "state:" + fips
    spaced = plugin.geography.resolve_place("North Carolina  State", world.transport)
    assert spaced.payload["state_fips"] == "37"
    lower = plugin.geography.resolve_place("georgia state", world.transport)
    assert lower.payload["state_fips"] == "13"
    assert world.calls == []


def test_texas_state_is_the_state_and_does_not_say_the_name_was_absent():
    world = World()
    for place in ("Texas State", "Georgia State", "North Carolina State"):
        result = make_service(world).place({"place": place, "year": 2024})
        assert result["error_type"] == "missing_key", place
        assert result["estimates_read"] is False
        assert result["geography"]["summary"] == "State"
        assert "No geography matched" not in json.dumps(result)
    assert result["geography"]["state_fips"] == "37"
    assert world.calls == []


def test_spring_tx_selects_the_one_exact_name():
    world = World()
    place_routes(world, [
        {"NAME": "Springtown city", "STATE": "48", "PLACE": "69800", "GEOID": "4869800"},
    ], [
        {"NAME": "Spring CDP", "STATE": "48", "PLACE": "69596", "GEOID": "4869596"},
    ])
    world.add("/data/2024/acs/acs5", ok_body(bundle_table("100", "5", name="Spring CDP, Texas")))
    result = with_key(world).place({"place": "Spring, TX", "year": 2024})
    assert result["resolved"] is True
    assert result["estimates_read"] is True
    assert result["geography"]["name"] == "Spring CDP"
    assert result["geography"]["place_fips"] == "69596"
    assert "Springtown" not in json.dumps(result.get("geography"))
    one = World()
    place_routes(one, [], [
        {"NAME": "Spring CDP", "STATE": "48", "PLACE": "69596", "GEOID": "4869596"},
    ])
    one.add("/data/2024/acs/acs5", ok_body(bundle_table("100", "5", name="Spring CDP, Texas")))
    chosen = with_key(one).place({"place": "Spring CDP, TX", "year": 2024})
    assert chosen["resolved"] is True
    assert chosen["estimates_read"] is True
    assert chosen["geography"]["name"] == "Spring CDP"
    assert chosen["geography"]["place_fips"] == "69596"
    assert chosen["geography"]["state_fips"] == "48"
    assert chosen["geography"]["county_fips"] is None


def test_a_cut_off_candidate_list_says_so():
    rows = []
    for index in range(21):
        rows.append({
            "NAME": "Washington city",
            "STATE": str((index % 50) + 1).zfill(2),
            "PLACE": str(index + 1).zfill(5),
            "GEOID": "x",
        })
    world = World()
    place_routes(world, rows)
    result = make_service(world).place({"place": "Washington", "year": 2024})
    assert result["resolved"] is False
    assert result["estimates_read"] is False
    assert result["truncated"] is True
    assert "cut off" in result["sentence"]
    assert result["candidates"][-1]["summary"] == "State"
    compared = make_service(world).compare({"place_a": "Washington", "place_b": "TX", "year": 2024})
    assert compared["estimates_read"] is False
    assert "cut off" in compared["sentence"]
    assert "cut off" in compared["place_a"]["sentence"]
    assert "cut off" not in compared["place_b"].get("sentence", "")


def test_catalog_page_leads_with_the_newest_detailed_tables():
    rows = []
    for year in (2009, 2010, 2023):
        rows.append({
            "c_vintage": year,
            "c_dataset": ["acs", "acs5", "cprofile"],
            "title": "ACS 5-Year Comparison Profiles",
            "c_isAvailable": True,
        })
    rows.append({
        "c_vintage": 2024,
        "c_dataset": ["acs", "acs5"],
        "title": "ACS 5-Year Detailed Tables",
        "c_isAvailable": True,
    })
    rows.append({
        "c_vintage": 2025,
        "c_dataset": ["acs", "acs1"],
        "title": "ACS 1-Year Detailed Tables",
        "c_isAvailable": True,
    })
    page = World()
    page.add("/data.json", ok_body({"dataset": rows}))
    listed = make_service(page).datasets({"query": "acs", "limit": 2})
    assert [(item["vintage_year"], item["dataset"]) for item in listed["datasets"]] == [
        (2025, "acs/acs1"),
        (2024, "acs/acs5"),
    ]
    assert listed["truncated"] is True
    assert listed["complete"] is False
    assert listed["sentence"] == "The dataset list was cut off. This result has 2 of 5 rows."


def test_group_rank_covers_the_table():
    groups = [
        {"name": "B05004", "description": "Median Age by Nativity and Citizenship Status by Sex"},
        {"name": "B12007C", "description": "Median Age at First Marriage (American Indian and Alaska Native Alone)"},
        {"name": "B01002G", "description": "Median Age by Sex (Two or More Races)"},
        {"name": "B01002", "description": "Median Age by Sex"},
        {"name": "B07002", "description": "Mobility and median age for the current residence"},
    ]
    ranked = plugin.acs.rank_groups(groups, "median age")
    assert [item["name"] for item in ranked] == ["B01002", "B05004", "B01002G", "B12007C", "B07002"]
    assert len(plugin.acs.group_rank(ranked[0], "median age")) == len(plugin.acs.GROUP_RANK)


def test_median_age_search_includes_the_total_estimate():
    groups = {"groups": [
        {"name": "B05004", "description": "Median Age by Nativity and Citizenship Status by Sex"},
        {"name": "B12007C", "description": "Median Age at First Marriage (American Indian and Alaska Native Alone)"},
        {"name": "B12007B", "description": "Median Age at First Marriage (Black or African American Alone)"},
        {"name": "B01002", "description": "Median Age by Sex"},
    ]}
    opened = []

    def transport(url, cap="data", timeout=20):
        if url.endswith("/groups.json"):
            return ok_body(groups)
        name = url.rstrip("/").rsplit("/", 1)[-1].replace(".json", "")
        opened.append(name)
        if name == "B01002":
            variables = {
                "B01002_001MA": {"label": "Annotation of Margin of Error!!Median age --!!Total:", "concept": "Median Age by Sex"},
                "B01002_001EA": {"label": "Annotation of Estimate!!Median age --!!Total:", "concept": "Median Age by Sex"},
                "B01002_002E": {"label": "Estimate!!Median age --!!Male", "concept": "Median Age by Sex"},
                "B01002_001E": {"label": "Estimate!!Median age --!!Total:", "concept": "Median Age by Sex"},
            }
        else:
            variables = {name + "_001EA": {"label": "Annotation of Estimate!!Median age", "concept": "Median Age"}}
        return ok_body({"variables": variables})

    svc = plugin.service.Census(transport=transport, key_reader=lambda: (None, "missing"), clock=lambda: CLOCK)
    result = svc.variables({"query": "median age", "year": 2024})
    assert result["ok"] is True
    assert result["complete"] is False
    assert result["groups_opened"] == 3
    assert result["groups_matched"] == 4
    assert opened[0] == "B01002"
    ids = [item["variable_id"] for item in result["variables"]]
    assert ids[0] == "B01002_001E"
    assert "B01002_001E" in ids


def test_a_dataset_page_that_is_cut_off_says_so():
    page = World()
    page.add("/data.json", ok_body({"dataset": [
        {"c_vintage": 2024, "c_dataset": ["acs", "acs5"], "title": "ACS 5-Year", "c_isAvailable": True},
        {"c_vintage": 2024, "c_dataset": ["acs", "acs1"], "title": "ACS 1-Year", "c_isAvailable": True},
        {"c_vintage": 2023, "c_dataset": ["acs", "acs5"], "title": "ACS 5-Year", "c_isAvailable": True},
    ]}))
    listed = make_service(page).datasets({"query": "acs", "limit": 1})
    assert listed["truncated"] is True
    assert listed["sentence"] == "The dataset list was cut off. This result has 1 of 3 rows."
    full = make_service(page).datasets({"query": "acs"})
    assert full["truncated"] is False
    assert "sentence" not in full


def test_a_variable_page_that_is_cut_off_says_so():
    groups = {"groups": [{"name": "B19013", "description": "Income"}]}

    def transport(url, cap="data", timeout=20):
        if url.endswith("/groups.json"):
            return ok_body(groups)
        return ok_body({"variables": {
            "B19013_001E": {"label": "Estimate!!Median income", "concept": "Income"},
            "B19013_001M": {"label": "Margin!!Median income", "concept": "Income"},
        }})

    svc = plugin.service.Census(transport=transport, key_reader=lambda: (None, "missing"), clock=lambda: CLOCK)
    result = svc.variables({"query": "income", "year": 2024, "limit": 1})
    assert result["ok"] is True
    assert result["complete"] is False
    assert result["search_capped"] is True
    assert len(result["variables"]) == 1
    assert result["sentence"].endswith("The variable list was cut off.")
    assert result["estimates_read"] is False


def test_stem_result_covers_every_shape():
    york = {"name": "York city"}
    yorktown = {"name": "Yorktown city"}
    cases = {
        "none": ([], False, 0),
        "one_exact": ([york], False, 1),
        "one_exact_among_prefixes": ([york, yorktown], False, 2),
        "many_exact": ([york, {"name": "York town"}], False, 2),
        "one_longer": ([yorktown], False, 1),
        "many_longer": ([yorktown, {"name": "Yorkshire city"}], False, 2),
        "truncated": ([york], True, 1),
    }
    assert set(cases) == set(plugin.geography.STEM_RESULTS)
    for shape, (rows, truncated, count) in cases.items():
        assert plugin.geography.stem_result(rows, "York", truncated, count) == shape, shape
    assert plugin.geography.stem_result([york], "York", False, 5) == "truncated"


def test_ordinary_place_typing_still_names_one_place():
    samples = (
        "  Austin, TX  ",
        "Austin, TX;",
        "Austin, TX # note",
        "Austin, TX // note",
        "austin, tx",
        "Ａｕｓｔｉｎ, ＴＸ",
    )
    for text in samples:
        world = World()
        place_routes(world, [austin_row()])
        world.add("/data/2024/acs/acs5", ok_body(bundle_table("100", "5")))
        result = with_key(world).place({"place": text, "year": 2024})
        assert result["resolved"] is True, text
        assert result["geography"]["name"] == "Austin city", text
        assert result["estimates_read"] is True
        where = world.calls[0]["query"]["where"][0]
        assert where.startswith("UPPER(NAME) IN (")
        assert "'AUSTIN CITY'" in where
        assert "LIKE" not in where
    refused = World()
    for text in ("https://api.census.gov/data/2024/acs/acs5", "Austin; TX"):
        found = plugin.geography.resolve_place(text, refused.transport)
        assert found.kind == "error", text
    assert refused.calls == []
    blank = World()
    place_routes(blank, [])
    missed = make_service(blank).place({"place": "東京", "year": 2024})
    assert missed["estimates_read"] is False
    assert missed["sentence"] == "No geography matched that name, so no estimate was read."


def test_place_spellings_select_the_census_name():
    """Every PLACE_SPELLINGS row, and every PLACE_NONMATCHES row."""
    assert plugin.geography.PLACE_SPELLINGS
    assert plugin.geography.PLACE_NONMATCHES
    for asked, official in plugin.geography.PLACE_SPELLINGS:
        assert plugin.geography.place_name_equal(official, asked), asked
        blob = " ".join(plugin.geography.place_literals(asked)).upper()
        assert official.upper() in blob, asked
        seen = []

        def transport(url, cap="geo", timeout=20, official=official, seen=seen):
            where = _where(url)
            seen.append(where)
            if official.upper() in where and "MapServer/4" in url:
                return ok_body(tiger([{
                    "NAME": official,
                    "STATE": "48",
                    "PLACE": "00001",
                    "GEOID": "4800001",
                }]))
            return ok_body(tiger([]))

        found = plugin.geography.resolve_place(asked + ", TX", transport)
        assert found.kind == "one", asked
        assert found.payload["name"] == official
        assert found.payload["place_fips"] == "00001"
        assert any(
            official.upper() in where and where.startswith("UPPER(NAME) IN (")
            for where in seen
        ), asked
    for asked, official in plugin.geography.PLACE_NONMATCHES:
        assert plugin.geography.place_name_equal(official, asked) is False, asked


def test_two_kansas_cities_stay_candidates_until_the_state_is_named():
    both = World()
    place_routes(both, [
        {"NAME": "Kansas City city", "STATE": "29", "PLACE": "38000", "GEOID": "2938000"},
        {"NAME": "Kansas City city", "STATE": "20", "PLACE": "36000", "GEOID": "2036000"},
    ])
    found = plugin.geography.resolve_place("Kansas City", both.transport)
    assert found.kind == "candidates"
    assert found.payload["estimates_read"] is False
    assert found.payload["match_count"] == 2
    names = [row["name"] for row in found.payload["candidates"]]
    assert names.count("Kansas City city") == 2
    one = World()
    place_routes(one, [
        {"NAME": "Kansas City city", "STATE": "29", "PLACE": "38000", "GEOID": "2938000"},
    ])
    chosen = plugin.geography.resolve_place("Kansas City, MO", one.transport)
    assert chosen.kind == "one"
    assert chosen.payload["name"] == "Kansas City city"
    assert chosen.payload["state_fips"] == "29"
    assert chosen.payload["place_fips"] == "38000"
    where = one.calls[0]["query"]["where"][0]
    assert where.startswith("UPPER(NAME) IN (")
    assert "'KANSAS CITY CITY'" in where
    assert "'KANSAS CITY'" in where


def test_a_borough_place_is_tried_after_the_county_layer_misses():
    world = World()
    world.add("State_County", ok_body(tiger([])))
    world.add("MapServer/4", ok_body(tiger([{
        "NAME": "State College borough",
        "STATE": "42",
        "PLACE": "73808",
        "GEOID": "4273808",
    }])))
    world.add("MapServer/5", ok_body(tiger([])))
    found = plugin.geography.resolve_place("State College borough, PA", world.transport)
    assert found.kind == "one"
    assert found.payload["name"] == "State College borough"
    assert found.payload["place_fips"] == "73808"
    assert found.payload["state_fips"] == "42"
    assert any("State_County" in call["path"] for call in world.calls)
    assert any("MapServer/4" in call["path"] for call in world.calls)


def test_place_fips_absent_from_tiger_is_not_an_empty_estimate():
    world = World()
    world.add("MapServer/4", ok_body(tiger([])))
    world.add("MapServer/5", ok_body(tiger([])))
    world.add("/data/2024/acs/acs5", HttpResult("empty_table", status=204, body=b""))
    result = with_key(world).get({
        "state_fips": "48",
        "place_fips": "99999",
        "variables": ["B01003_001E"],
        "year": 2024,
    })
    assert result["ok"] is True
    assert result["empty_row"] is False
    assert result["roster"] == "absent"
    assert result["estimates_read"] is False
    assert "99999" in result["sentence"]
    assert "absent from the Census roster" in result["sentence"]
    assert "HTTP 204" not in result["sentence"]


def test_a_roster_error_does_not_call_the_fips_an_empty_estimate():
    world = World()
    world.add("State_County", HttpResult("http_status", status=503, body=b""))
    world.add("/data/2024/acs/acs5", HttpResult("empty_table", status=204, body=b""))
    result = with_key(world).get({
        "state_fips": "48",
        "county_fips": "301",
        "variables": ["B01003_001E"],
        "year": 2024,
    })
    assert result["ok"] is False
    assert result["error_type"] == "http_status"
    assert result.get("empty_row") is not True
    assert result["estimates_read"] is False
    assert "absent from the Census roster" not in json.dumps(result)
    assert "33" not in json.dumps(result)


def test_an_annotation_margin_is_named_as_an_annotation():
    fig = plugin.acs.figure(
        "B01003_001E",
        "Total population",
        2024,
        "acs/acs1",
        {"B01003_001E": "31290831", "B01003_001M": "-555555555"},
    )
    assert fig["estimate"] == 31290831
    assert fig["margin_of_error"] is None
    assert fig["moe_annotation"] == "*****"
    assert "margin of error" not in fig["sentence"]
    assert fig["sentence"] == (
        "ACS 1-year vintage 2024 estimate B01003_001E. "
        "Margin variable B01003_001M is the annotation *****."
    )
    housing = plugin.acs.figure(
        "B25077_001E",
        "Median home value",
        2024,
        "acs/acs5",
        {"B25077_001E": "-666666666", "B25077_001M": "-222222222"},
    )
    assert housing["estimate"] is None
    assert housing["annotation"] == "-"
    assert housing["moe_annotation"] == "**"
    assert "margin of error" not in housing["sentence"]
    assert "**" in housing["sentence"]
    numeric = plugin.acs.figure(
        "B01003_001E",
        "Total population",
        2024,
        "acs/acs5",
        {"B01003_001E": "100", "B01003_001M": "5"},
    )
    assert numeric["sentence"] == (
        "ACS 5-year vintage 2024 estimate B01003_001E, with margin of error B01003_001M."
    )
    header = ["NAME", "B01003_001E", "B01003_001M", "B25077_001E", "B25077_001M"]
    row = ["Loving County, Texas", "33", "30", "-666666666", "-222222222"]
    built = plugin.acs.sheet(
        header,
        row,
        2024,
        "acs/acs5",
        {"name": "Loving County"},
        [("B01003_001E", "Total population"), ("B25077_001E", "Median home value")],
    )
    assert built["sentence"] == (
        "ACS 5-year vintage 2024. Each figure carries its variable id. "
        "A margin annotation stays an annotation."
    )
    assert built["figures"][0]["sentence"].endswith(", with margin of error B01003_001M.")
