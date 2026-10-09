"""Official consumption statistics for country-level demand (2A).

When a market is an FAOSTAT commodity, its per-country consumption comes from
FAOSTAT instead of web search -- the same idea as the Olive Oil 2A page,
which used official IOC/FAO consumption rather than LLM-found figures.

  Food Balances (FBS)       120 food items (olive oil, coffee, tea, wine,
                            beer, vegetable oils, sugar, ...) -- element
                            "Domestic supply quantity" (1000 t), to 2023
  Fertilizers by Product    23 products (urea, DAP, MOP potash, ...) --
                            apparent consumption = production + imports
                            - exports (t), to 2024. ("Agricultural Use"
                            is not used: it is missing for China and has
                            gaps, e.g. Brazil urea 0.44 Mt.)

The bulk files are downloaded once into data/official/ and boiled down to a
small JSON cache. The level is the average of the last 3 data years (so one
odd year -- e.g. the 2023 olive drought -- doesn't set it); it is moved to
the base year with the country's own trend (last 3 years vs the 3 before,
capped at +/-5% a year) and tagged `derived`. A value for the base year
itself is `verified_observed`. "Rest of World" = World total (FBS: FAO's
World row; fertilizers: sum of all countries) minus the listed countries.
"""
from __future__ import annotations

import csv
import io
import json
import re
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = ROOT / "data" / "official"
CACHE = DATA_DIR / "faostat_cache.json"
BULK_URL = "https://bulks-faostat.fao.org/production/{}"
MIN_YEAR = 2015
MAX_TREND = 0.05

DATASETS: dict[str, dict[str, Any]] = {
    "fbs": {
        "label": "FAOSTAT Food Balances",
        "file": "FoodBalanceSheets_E_All_Data_(Normalized).zip",
        "element": "Domestic supply quantity",
        "elements": ("Domestic supply quantity",),
        "to_tonnes": 1000.0,
        "page": "https://www.fao.org/faostat/en/#data/FBS",
    },
    "rfb": {
        "label": "FAOSTAT Fertilizers by Product",
        "file": "Inputs_FertilizersProduct_E_All_Data_(Normalized).zip",
        "element": "Apparent consumption (production + imports - exports)",
        "elements": ("Production", "Import quantity", "Export quantity"),
        "to_tonnes": 1.0,
        "page": "https://www.fao.org/faostat/en/#data/RFB",
    },
}
# Aggregates in FBS that are not one commodity.
_NOT_ONE_PRODUCT = {
    "grand total", "population", "miscellaneous", "vegetal products", "animal products", "stimulants",
    "vegetable oils", "oilcrops", "meat", "cereals - excluding beer", "sugar & sweeteners", "spices",
    "pulses", "starchy roots", "treenuts", "vegetables", "fish, seafood", "fruits - excluding wine",
    "alcoholic beverages", "milk - excluding butter", "aquatic products, other", "sugar crops",
}
# Our country names -> FAO area names.
_AREA_ALIASES = {
    "united states": "United States of America", "usa": "United States of America", "us": "United States of America",
    "united kingdom": "United Kingdom of Great Britain and Northern Ireland", "uk": "United Kingdom of Great Britain and Northern Ireland",
    "china": "China, mainland", "russia": "Russian Federation", "south korea": "Republic of Korea",
    "korea": "Republic of Korea", "north korea": "Democratic People's Republic of Korea",
    "iran": "Iran (Islamic Republic of)", "vietnam": "Viet Nam", "turkey": "Türkiye", "turkiye": "Türkiye",
    "netherlands": "Netherlands (Kingdom of the)", "taiwan": "China, Taiwan Province of",
    "hong kong": "China, Hong Kong SAR", "syria": "Syrian Arab Republic", "venezuela": "Venezuela (Bolivarian Republic of)",
    "bolivia": "Bolivia (Plurinational State of)", "tanzania": "United Republic of Tanzania", "laos": "Lao People's Democratic Republic",
    "moldova": "Republic of Moldova", "czech republic": "Czechia", "ivory coast": "Côte d'Ivoire",
    "cote d'ivoire": "Côte d'Ivoire", "palestine": "Palestine", "uae": "United Arab Emirates",
}
_REST_OF_WORLD = re.compile(r"^rest of (the )?world$", re.I)


def _download(file: str) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / file
    if not path.exists():
        urllib.request.urlretrieve(BULK_URL.format(file), path)
    return path


def _build_dataset(key: str) -> dict[str, dict[str, dict[str, float]]]:
    """{item: {area: {year: value_in_tonnes}}} -- countries only (FAO area
    code < 5000, minus the 'China' aggregate 351) plus a 'World' row."""
    spec = DATASETS[key]
    zf = zipfile.ZipFile(_download(spec["file"]))
    name = next(n for n in zf.namelist() if "All_Data" in n and n.endswith(".csv"))
    raw: dict[tuple[str, str, str], dict[str, float]] = {}
    with zf.open(name) as f:
        reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8", errors="replace"))
        for row in reader:
            if row["Element"] not in spec["elements"]:
                continue
            try:
                year, value, code = int(row["Year"]), float(row["Value"]), int(row["Area Code"])
            except (TypeError, ValueError):
                continue
            if year < MIN_YEAR or (code >= 5000 and code != 5000) or code == 351:
                continue
            area = "World" if code == 5000 else row["Area"]
            raw.setdefault((row["Item"], area, str(year)), {})[row["Element"]] = value * spec["to_tonnes"]
    out: dict[str, dict[str, dict[str, float]]] = {}
    for (item, area, year), els in raw.items():
        if key == "rfb":
            if "Production" not in els and "Import quantity" not in els:
                continue
            value = els.get("Production", 0.0) + els.get("Import quantity", 0.0) - els.get("Export quantity", 0.0)
            if value <= 0:
                continue
        else:
            value = els.get(spec["elements"][0])
            if value is None:
                continue
        out.setdefault(item, {}).setdefault(area, {})[year] = value
    if key == "rfb":  # no World row in this dataset -- sum the countries
        for item, areas in out.items():
            world: dict[str, float] = {}
            for area, series in areas.items():
                for year, v in series.items():
                    world[year] = world.get(year, 0.0) + v
            # The latest years are often only partly reported (China had no
            # 2024 urea row yet): drop years well below the best earlier year.
            best = 0.0
            for year in sorted(world):
                if best and world[year] < 0.85 * best:
                    del world[year]
                    continue
                best = max(best, world[year])
            areas["World"] = world
    return out


def load_cache() -> dict[str, Any]:
    if CACHE.exists():
        return json.loads(CACHE.read_text(encoding="utf-8"))
    cache = {key: _build_dataset(key) for key in DATASETS}
    CACHE.write_text(json.dumps(cache), encoding="utf-8")
    return cache


def candidate_items(cache: dict[str, Any]) -> list[tuple[str, str]]:
    return [(key, item) for key in DATASETS for item in sorted(cache.get(key) or {})
            if item.lower() not in _NOT_ONE_PRODUCT]


def _words(text: str) -> set[str]:
    stop = {"and", "products", "product", "including", "excl", "incl", "market", "global", "fertilizer", "fertilizers", "the"}
    return {w.rstrip("s") for w in re.findall(r"[a-z]+", text.lower()) if w not in stop}


def match_item(market: str, cache: dict[str, Any], llm: Any | None = None) -> tuple[str, str] | None:
    """(dataset, item) when the market IS one FAOSTAT item, else None.
    Word overlap shortlists; the LLM (when given) must confirm same scope."""
    mw = _words(market)
    shortlist = [(k, i) for k, i in candidate_items(cache) if mw and mw <= _words(i)]
    if not shortlist:
        return None
    if llm is None:
        exact = [(k, i) for k, i in shortlist if _words(i) == mw]
        return exact[0] if len(exact) == 1 else None
    system = (
        "You check whether a market is measured by one official statistics item. Answer yes ONLY if "
        "the item covers the same product as the market -- not a broader group, not a raw material "
        "of it, not a narrower part. Example: market 'Olive Oil' = item 'Olive Oil' (yes); market "
        "'Chocolate' vs item 'Cocoa Beans and products' (no, broader/raw material); market 'Urea "
        "Fertilizer' = item 'Urea' in fertilizers (yes)."
    )
    options = "\n".join(f"{n}. [{DATASETS[k]['label']}] {i}" for n, (k, i) in enumerate(shortlist, 1))
    user = (f"Market: {market}\nItems:\n{options}\n\n"
            'Return JSON: {"match": <item number, or 0 if none is the same product>, "reason": "<short>"}')
    try:
        data = llm.complete_json(system, user)
        n = int(data.get("match") or 0)
    except Exception:  # noqa: BLE001 -- no match is the safe answer
        return None
    return shortlist[n - 1] if 1 <= n <= len(shortlist) else None


def _area_for(country: str, areas: dict[str, Any]) -> str | None:
    alias = _AREA_ALIASES.get(country.strip().lower())
    if alias and alias in areas:
        return alias
    if country in areas:
        return country
    low = {a.lower(): a for a in areas}
    return low.get(country.strip().lower())


def _project(series: dict[str, float], base_year: int) -> tuple[float, int, float]:
    """(value at base year, latest data year, yearly trend applied).
    Level = mean of the last 3 data years (centred one year before the
    latest); trend = last 3 years vs the 3 before, capped."""
    pts = {int(y): v for y, v in series.items() if v and v > 0}
    if not pts:
        return 0.0, 0, 0.0
    if base_year in pts:
        return pts[base_year], base_year, 0.0
    last = max((y for y in pts if y <= base_year), default=min(pts))
    recent = [pts[y] for y in (last - 2, last - 1, last) if y in pts]
    before = [pts[y] for y in (last - 5, last - 4, last - 3) if y in pts]
    level = sum(recent) / len(recent)
    centre = last - 1 if len(recent) == 3 else last
    trend = 0.0
    if len(recent) == 3 and len(before) == 3:
        trend = (level / (sum(before) / 3)) ** (1 / 3) - 1
        trend = max(-MAX_TREND, min(MAX_TREND, trend))
    return level * (1 + trend) ** (base_year - centre), last, trend


def country_facts(
    dataset: str, item: str, countries: list[str], base_year: int, cache: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """{country: evidence fact in tonnes} for every listed country FAO covers;
    "Rest of World" = World minus the listed countries."""
    spec = DATASETS[dataset]
    areas = (cache.get(dataset) or {}).get(item) or {}
    facts: dict[str, dict[str, Any]] = {}
    covered = 0.0
    for c in countries:
        if _REST_OF_WORLD.match(c.strip()):
            continue
        area = _area_for(c, areas)
        if not area or not areas[area]:
            continue
        value, year, trend = _project(areas[area], base_year)
        covered += value
        facts[c] = _fact(spec, item, area, value, year, trend, base_year, areas[area][str(year)])
    world = areas.get("World")
    row = next((c for c in countries if _REST_OF_WORLD.match(c.strip())), None)
    if row and world:
        w_value, w_year, w_trend = _project(world, base_year)
        rest = w_value - covered
        if rest > 0:
            facts[row] = _fact(spec, item, "World minus listed countries", rest, w_year, w_trend, base_year,
                               world[str(w_year)])
    return facts


def _fact(spec: dict[str, Any], item: str, area: str, value: float, year: int, trend: float,
          base_year: int, raw: float) -> dict[str, Any]:
    status = "verified_observed" if year >= base_year else "derived"
    basis = (f"{spec['label']} '{item}', {spec['element']}, {area}, {year}: {raw:,.0f} t"
             + (f"; {base_year} = average of the last 3 data years moved at {trend:+.1%}/yr "
                "(that country's own trend)" if year < base_year else ""))
    return {
        "value": round(value, 3), "unit": "tonnes", "year": year,
        "evidence_type": status, "verification_status": status, "confidence": 0.9 if status == "verified_observed" else 0.8,
        "source": spec["label"], "source_name": spec["label"], "source_url": spec["page"],
        "citation": basis, "evidence_text": basis, "derivation_basis": basis,
        "source_type": "official_statistics", "is_direct_figure": year >= base_year,
    }


def official_country_demand(
    market: str, countries: list[str], base_year: int, llm: Any | None = None,
) -> dict[str, Any] | None:
    """{dataset, item, label, facts} or None when the market isn't an FAOSTAT
    item (or the data can't be loaded)."""
    try:
        cache = load_cache()
    except Exception:  # noqa: BLE001 -- no official data; caller uses web search
        return None
    hit = match_item(market, cache, llm)
    if not hit:
        return None
    dataset, item = hit
    facts = country_facts(dataset, item, countries, base_year, cache)
    if not facts:
        return None
    return {"dataset": dataset, "item": item, "label": DATASETS[dataset]["label"],
            "element": DATASETS[dataset]["element"], "facts": facts}
