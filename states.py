"""State and state-equivalent FIPS codes. Static table, no network."""

from __future__ import annotations

# Postal abbreviation, 2-digit FIPS, official name. DC is 11. Puerto Rico is 72.
STATES = (
    ("AL", "01", "Alabama"),
    ("AK", "02", "Alaska"),
    ("AZ", "04", "Arizona"),
    ("AR", "05", "Arkansas"),
    ("CA", "06", "California"),
    ("CO", "08", "Colorado"),
    ("CT", "09", "Connecticut"),
    ("DE", "10", "Delaware"),
    ("DC", "11", "District of Columbia"),
    ("FL", "12", "Florida"),
    ("GA", "13", "Georgia"),
    ("HI", "15", "Hawaii"),
    ("ID", "16", "Idaho"),
    ("IL", "17", "Illinois"),
    ("IN", "18", "Indiana"),
    ("IA", "19", "Iowa"),
    ("KS", "20", "Kansas"),
    ("KY", "21", "Kentucky"),
    ("LA", "22", "Louisiana"),
    ("ME", "23", "Maine"),
    ("MD", "24", "Maryland"),
    ("MA", "25", "Massachusetts"),
    ("MI", "26", "Michigan"),
    ("MN", "27", "Minnesota"),
    ("MS", "28", "Mississippi"),
    ("MO", "29", "Missouri"),
    ("MT", "30", "Montana"),
    ("NE", "31", "Nebraska"),
    ("NV", "32", "Nevada"),
    ("NH", "33", "New Hampshire"),
    ("NJ", "34", "New Jersey"),
    ("NM", "35", "New Mexico"),
    ("NY", "36", "New York"),
    ("NC", "37", "North Carolina"),
    ("ND", "38", "North Dakota"),
    ("OH", "39", "Ohio"),
    ("OK", "40", "Oklahoma"),
    ("OR", "41", "Oregon"),
    ("PA", "42", "Pennsylvania"),
    ("RI", "44", "Rhode Island"),
    ("SC", "45", "South Carolina"),
    ("SD", "46", "South Dakota"),
    ("TN", "47", "Tennessee"),
    ("TX", "48", "Texas"),
    ("UT", "49", "Utah"),
    ("VT", "50", "Vermont"),
    ("VA", "51", "Virginia"),
    ("WA", "53", "Washington"),
    ("WV", "54", "West Virginia"),
    ("WI", "55", "Wisconsin"),
    ("WY", "56", "Wyoming"),
    ("PR", "72", "Puerto Rico"),
)

BY_ABBR = {abbr: (fips, name) for abbr, fips, name in STATES}
BY_NAME = {name.lower(): (abbr, fips, name) for abbr, fips, name in STATES}

# Bare names that were also an incorporated place or a CDP on 2026-11-03.
# Exact NAME values on TIGERweb layers 4 and 5. Names in this set always stay
# candidates, including the state. A bare name outside this set is checked live
# for an exact place or CDP before it can be read as the state. A postal
# abbreviation stays the state. Any name in BY_NAME followed by State stays
# the state, including names outside the set above.
AMBIGUOUS_STATE_NAMES = frozenset({
    "california",
    "delaware",
    "florida",
    "indiana",
    "iowa",
    "kansas",
    "louisiana",
    "maine",
    "nevada",
    "new hampshire",
    "new york",
    "ohio",
    "oklahoma",
    "oregon",
    "puerto rico",
    "tennessee",
    "vermont",
    "virginia",
    "washington",
    "wyoming",
})

DC_ALIASES = frozenset({
    "dc",
    "d.c.",
    "district of columbia",
    "washington, dc",
    "washington, d.c.",
    "washington dc",
    "washington d.c.",
})
