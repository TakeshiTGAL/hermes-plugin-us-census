"""Tool schemas. The first sentence of each description is a string constant."""

CENSUS_DATASETS = {
    "name": "census_datasets",
    "description": (
        "List Census datasets and their vintage years. "
        "ACS rows include the vintage year, the dataset path, and whether the Census list marks the dataset available. "
        "A catalog that hits the size cap is incomplete, and an empty list is not claimed. "
        "Population, household income, housing, geography, and variable metadata stay on the other tools. "
        "This product uses the Census Bureau Data API but is not endorsed or certified by the Census Bureau."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "query": {
                "type": "string",
                "description": "Optional keyword. The default is acs. An empty string returns the first page of the whole catalog.",
            },
            "limit": {
                "type": "integer",
                "description": "Page size from 1 through 40. The default is 40.",
            },
        },
    },
}

CENSUS_GEOGRAPHIES = {
    "name": "census_geographies",
    "description": (
        "List geography levels for one ACS year. "
        "Levels for an ACS dataset, with the vintage year, including state, county, and place. "
        "The year on the result is the ACS vintage, with its variable geography, not a same-day headcount. "
        "FIPS geography for population, income, household, housing, and margin of error lives on the estimate tools. "
        "This product uses the Census Bureau Data API but is not endorsed or certified by the Census Bureau."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "year": {
                "type": "integer",
                "description": "ACS vintage year. Leave it out to use the newest listed vintage for the dataset.",
            },
            "dataset": {
                "type": "string",
                "description": "acs/acs5 or acs/acs1. The default is acs/acs5.",
            },
        },
    },
}

CENSUS_VARIABLES = {
    "name": "census_variables",
    "description": (
        "Search ACS variable groups and codes. "
        "Keyless metadata for a variable id, a group id, or a keyword such as population, household income, median age, or housing. "
        "The result carries the vintage year and the variable id, and the margin of error id when the metadata lists it. "
        "A capped search says how many groups matched and how many were opened. It does not claim the catalog has no variable. "
        "This product uses the Census Bureau Data API but is not endorsed or certified by the Census Bureau."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "code": {
                "type": "string",
                "description": "One variable id, such as the total-population estimate id. This lookup uses keyless metadata.",
            },
            "query": {
                "type": "string",
                "description": "Keyword, group id, or variable id when code is omitted.",
            },
            "year": {
                "type": "integer",
                "description": "ACS vintage year. Leave it out to use the newest listed vintage.",
            },
            "dataset": {
                "type": "string",
                "description": "acs/acs5 or acs/acs1. The default is acs/acs5.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum variable rows, from 1 through 40. The default is 20.",
            },
        },
    },
}

CENSUS_PLACE = {
    "name": "census_place",
    "description": (
        "Get US population, income, age, and housing for a place. "
        "One ACS sheet for a single matched geography: total population, median household income, median age, housing units, households, and median home value. "
        "Each figure has its vintage year, variable id, and margin of error. "
        "A street address uses the Census geocoder. A place or county name uses Census TIGERweb. "
        "Several matches return candidates and no estimates. Zero matches do not invent a number. "
        "The figure is an ACS vintage estimate. State FIPS and the dataset stay on the sheet. "
        "A county match includes county FIPS. A city from the place layer leaves county FIPS empty. "
        "An address geocode includes the county of that point. "
        "This product uses the Census Bureau Data API but is not endorsed or certified by the Census Bureau."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "required": ["place"],
        "properties": {
            "place": {
                "type": "string",
                "description": "City, county, state, or a street address that starts with a number.",
            },
            "year": {
                "type": "integer",
                "description": "ACS vintage year. Leave it out to use the newest listed vintage. The result still names that vintage.",
            },
            "dataset": {
                "type": "string",
                "description": "acs/acs5 or acs/acs1. The default is acs/acs5 because the 1-year tables omit many small places.",
            },
        },
    },
}

CENSUS_GET = {
    "name": "census_get",
    "description": (
        "Fetch ACS estimates for one place and year. "
        "One state, county, or place, and up to 10 variable ids, each with its vintage year and margin of error. "
        "State, county, and place use FIPS. A wildcard geography is refused. "
        "Population, household income, median age, and housing ids are the usual detailed-table estimates. "
        "The figure is an ACS vintage estimate for that dataset and year. "
        "This product uses the Census Bureau Data API but is not endorsed or certified by the Census Bureau."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "required": ["state_fips", "variables"],
        "properties": {
            "state_fips": {
                "type": "string",
                "description": "2-digit state FIPS, such as 48 for Texas.",
            },
            "county_fips": {
                "type": "string",
                "description": "Optional 3-digit county FIPS. Omit it when you pass a place.",
            },
            "place_fips": {
                "type": "string",
                "description": "Optional 5-digit place FIPS. Requires state_fips.",
            },
            "variables": {
                "type": "array",
                "description": "1 to 10 estimate or margin variable ids. Each estimate also requests its margin id.",
                "items": {"type": "string"},
            },
            "year": {
                "type": "integer",
                "description": "ACS vintage year. Leave it out to use the newest listed vintage.",
            },
            "dataset": {
                "type": "string",
                "description": "acs/acs5 or acs/acs1. The default is acs/acs5.",
            },
        },
    },
}

CENSUS_COMPARE = {
    "name": "census_compare",
    "description": (
        "Compare two US places in one ACS year. "
        "Same vintage year for both geographies, with population, median household income, median age, and housing, each with its variable id and margin of error. "
        "Different years are refused before any request. "
        "Ambiguous names return candidates and no estimates. "
        "State FIPS and the dataset stay on each sheet. A county match includes county FIPS. "
        "A city from the place layer leaves county FIPS empty. The figures are ACS vintage estimates. "
        "This product uses the Census Bureau Data API but is not endorsed or certified by the Census Bureau."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "required": ["place_a", "place_b"],
        "properties": {
            "place_a": {"type": "string", "description": "First place, county, state, or street address."},
            "place_b": {"type": "string", "description": "Second place, county, state, or street address."},
            "year": {
                "type": "integer",
                "description": "One ACS vintage year for both places.",
            },
            "year_a": {
                "type": "integer",
                "description": "Optional year for the first place. It must equal year_b.",
            },
            "year_b": {
                "type": "integer",
                "description": "Optional year for the second place. It must equal year_a.",
            },
            "dataset": {
                "type": "string",
                "description": "acs/acs5 or acs/acs1. The default is acs/acs5. Both places use this one dataset.",
            },
        },
    },
}

ALL_SCHEMAS = (
    CENSUS_DATASETS,
    CENSUS_GEOGRAPHIES,
    CENSUS_VARIABLES,
    CENSUS_PLACE,
    CENSUS_GET,
    CENSUS_COMPARE,
)
