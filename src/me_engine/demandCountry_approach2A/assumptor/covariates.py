"""Country covariates for the assumptor: World Bank population, GDP per
capita (PPP), urban share and region for every country, keyed by ISO3, plus
a name resolver for FAOSTAT / free-text country names.

Fetched once and cached at output/assumptor/wb_covariates.json.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx

_WB = "https://api.worldbank.org/v2"
_INDICATORS = {"population": "SP.POP.TOTL", "gdp_pc_ppp": "NY.GDP.PCAP.PP.CD", "urban_pct": "SP.URB.TOTL.IN.ZS"}
_ALIASES = {
    "united states of america": "USA", "united states": "USA", "usa": "USA", "us": "USA",
    "united kingdom of great britain and northern ireland": "GBR", "united kingdom": "GBR", "uk": "GBR",
    "china, mainland": "CHN", "china": "CHN", "china, hong kong sar": "HKG", "hong kong": "HKG",
    "china, macao sar": "MAC", "china, taiwan province of": "TWN", "taiwan": "TWN",
    "republic of korea": "KOR", "south korea": "KOR", "korea": "KOR",
    "democratic people's republic of korea": "PRK", "north korea": "PRK",
    "iran (islamic republic of)": "IRN", "iran": "IRN", "russian federation": "RUS", "russia": "RUS",
    "türkiye": "TUR", "turkiye": "TUR", "turkey": "TUR", "viet nam": "VNM", "vietnam": "VNM",
    "syrian arab republic": "SYR", "syria": "SYR", "egypt": "EGY", "netherlands (kingdom of the)": "NLD",
    "netherlands": "NLD", "bolivia (plurinational state of)": "BOL", "venezuela (bolivarian republic of)": "VEN",
    "united republic of tanzania": "TZA", "tanzania": "TZA", "republic of moldova": "MDA", "moldova": "MDA",
    "democratic republic of the congo": "COD", "congo": "COG", "micronesia (federated states of)": "FSM",
    "côte d'ivoire": "CIV", "cote d'ivoire": "CIV", "czechia": "CZE", "czech republic": "CZE",
    "lao people's democratic republic": "LAO", "laos": "LAO", "slovakia": "SVK", "kyrgyzstan": "KGZ",
    "gambia": "GMB", "bahamas": "BHS", "yemen": "YEM", "saint lucia": "LCA", "uae": "ARE",
    "united arab emirates": "ARE", "palestine": "PSE", "state of palestine": "PSE",
}


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "").strip().lower())


def _cache_path(root: Path | None) -> Path:
    return (Path(root) if root else Path(".")) / "output" / "assumptor" / "wb_covariates.json"


def fetch() -> dict[str, Any]:
    countries = httpx.get(f"{_WB}/country", params={"format": "json", "per_page": 400}, timeout=60).json()[1]
    out: dict[str, dict[str, Any]] = {}
    names: dict[str, str] = {}
    for c in countries:
        region = str((c.get("region") or {}).get("value") or "").strip()
        if region == "Aggregates" or not c.get("id"):
            continue
        out[c["id"]] = {"name": c["name"], "region": region}
        names[_norm(c["name"])] = c["id"]
        names[_norm(c["name"].split(",")[0])] = c["id"]
    for feature, code in _INDICATORS.items():
        rows = httpx.get(f"{_WB}/country/all/indicator/{code}",
                         params={"format": "json", "per_page": 20000, "date": "2015:2024"}, timeout=120).json()[1]
        latest: dict[str, tuple[int, float]] = {}
        for r in rows:
            iso, val = r.get("countryiso3code"), r.get("value")
            if iso in out and val is not None and (iso not in latest or int(r["date"]) > latest[iso][0]):
                latest[iso] = (int(r["date"]), float(val))
        for iso, (_, val) in latest.items():
            out[iso][feature] = val
    return {"countries": out, "names": names}


class Covariates:
    def __init__(self, data: dict[str, Any]) -> None:
        self.countries: dict[str, dict[str, Any]] = data["countries"]
        self.names: dict[str, str] = data["names"]

    @classmethod
    def load(cls, root: Path | None = None) -> "Covariates":
        path = _cache_path(root)
        if path.exists():
            return cls(json.loads(path.read_text(encoding="utf-8")))
        data = fetch()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return cls(data)

    def iso3(self, name: str) -> str | None:
        key = _norm(name)
        if key in _ALIASES:
            return _ALIASES[key]
        if key in self.names:
            return self.names[key]
        if re.fullmatch(r"[a-z]{3}", key) and key.upper() in self.countries:
            return key.upper()
        stripped = _norm(re.sub(r"\(.*?\)", "", key).replace("the ", ""))
        return self.names.get(stripped)

    def get(self, name_or_iso: str) -> dict[str, Any] | None:
        iso = name_or_iso if name_or_iso in self.countries else self.iso3(name_or_iso)
        c = self.countries.get(iso or "")
        if not c or not c.get("population") or not c.get("gdp_pc_ppp"):
            return None
        return {**c, "iso3": iso}
