"""Country -> region mapping for the country -> region -> global rollup.

Uses CMI's five standard report regions (the same names as
`generation/skeleton.py`'s `REGIONS`). Deterministic, no LLM: a country the
table doesn't know lands in "Rest of World" so it is still counted in the
global total, never dropped.
"""
from __future__ import annotations

import re

NORTH_AMERICA = "North America"
LATIN_AMERICA = "Latin America"
EUROPE = "Europe"
ASIA_PACIFIC = "Asia Pacific"
MIDDLE_EAST_AFRICA = "Middle East & Africa"
REST_OF_WORLD = "Rest of World"

REGION_ORDER = [NORTH_AMERICA, LATIN_AMERICA, EUROPE, ASIA_PACIFIC, MIDDLE_EAST_AFRICA, REST_OF_WORLD]

_COUNTRIES: dict[str, list[str]] = {
    NORTH_AMERICA: ["united states", "us", "usa", "u.s.", "u.s.a.", "america", "canada"],
    LATIN_AMERICA: [
        "mexico", "brazil", "argentina", "chile", "colombia", "peru", "venezuela", "ecuador",
        "uruguay", "paraguay", "bolivia", "guatemala", "cuba", "dominican republic", "costa rica",
        "panama", "puerto rico",
    ],
    EUROPE: [
        "united kingdom", "uk", "u.k.", "great britain", "britain", "england", "germany", "france",
        "italy", "spain", "portugal", "netherlands", "belgium", "luxembourg", "switzerland", "austria",
        "ireland", "sweden", "norway", "denmark", "finland", "iceland", "poland", "czech republic",
        "czechia", "slovakia", "hungary", "romania", "bulgaria", "greece", "croatia", "slovenia",
        "serbia", "ukraine", "russia", "belarus", "turkey", "turkiye", "estonia", "latvia", "lithuania",
        "benelux", "nordics", "eu", "european union",
    ],
    ASIA_PACIFIC: [
        "china", "japan", "india", "south korea", "korea", "taiwan", "hong kong", "singapore",
        "malaysia", "thailand", "indonesia", "philippines", "vietnam", "viet nam", "australia",
        "new zealand", "pakistan", "bangladesh", "sri lanka", "nepal", "myanmar", "cambodia",
        "kazakhstan", "asean",
    ],
    MIDDLE_EAST_AFRICA: [
        "saudi arabia", "united arab emirates", "uae", "qatar", "kuwait", "oman", "bahrain", "gcc",
        "israel", "iran", "iraq", "jordan", "lebanon", "egypt", "south africa", "nigeria", "kenya",
        "morocco", "algeria", "ethiopia", "ghana", "tanzania", "angola", "tunisia", "libya",
        "middle east", "africa",
    ],
}

_LOOKUP = {name: region for region, names in _COUNTRIES.items() for name in names}

# "Rest of <X>" style buckets: match on the X part.
_REST_HINTS = [
    ("north america", NORTH_AMERICA),
    ("latin america", LATIN_AMERICA), ("south america", LATIN_AMERICA), ("central america", LATIN_AMERICA),
    ("europe", EUROPE),
    ("asia", ASIA_PACIFIC), ("asean", ASIA_PACIFIC), ("pacific", ASIA_PACIFIC),
    ("middle east", MIDDLE_EAST_AFRICA), ("africa", MIDDLE_EAST_AFRICA), ("gcc", MIDDLE_EAST_AFRICA),
]


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", str(name).strip().lower())


def region_for(country: str) -> str:
    key = _norm(country)
    if key in _LOOKUP:
        return _LOOKUP[key]
    stripped = key.replace("the ", "", 1) if key.startswith("the ") else key
    if stripped in _LOOKUP:
        return _LOOKUP[stripped]
    for hint, region in _REST_HINTS:
        if hint in key:
            return region
    return REST_OF_WORLD


def country_region_map(countries: list[str]) -> dict[str, str]:
    return {c: region_for(c) for c in countries}


def rollup_regions(country_totals: dict[str, float]) -> dict[str, float]:
    """Sum country totals into regions, in REGION_ORDER, omitting empty regions."""
    sums: dict[str, float] = {}
    for country, value in country_totals.items():
        region = region_for(country)
        sums[region] = sums.get(region, 0.0) + float(value or 0.0)
    return {r: round(sums[r], 4) for r in REGION_ORDER if r in sums}
