"""Wikidata SPARQL — free, no API key."""
from __future__ import annotations

from vendor_intel.placeholders._http import get_json

WIKIDATA_ENABLED = True
WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"

_SPARQL_PARENT = """
SELECT ?parentLabel WHERE {{
  ?item rdfs:label "{name}"@en .
  ?item wdt:P31/wdt:P279* wd:Q4830453 .
  ?item wdt:P749 ?parent .
  ?parent rdfs:label ?parentLabel .
  FILTER(LANG(?parentLabel) = "en")
}}
LIMIT 3
"""


def is_enabled() -> bool:
    return WIKIDATA_ENABLED


async def lookup_parent_org(company_name: str) -> tuple[str | None, str | None]:
    if not WIKIDATA_ENABLED:
        return None, None
    safe = company_name.replace('"', "").strip()
    if not safe:
        return None, None
    query = _SPARQL_PARENT.format(name=safe)
    try:
        data = await get_json(
            WIKIDATA_SPARQL_URL,
            params={"query": query, "format": "json"},
            headers={"Accept": "application/sparql-results+json"},
        )
        bindings = data.get("results", {}).get("bindings", [])
        if bindings:
            label = bindings[0].get("parentLabel", {}).get("value")
            return label, "https://www.wikidata.org"
    except Exception:
        pass
    return None, None
