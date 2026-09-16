"""Real, source-backed trade evidence from the UN Comtrade public API.

This is the provenance backbone for the Segmentation Demo: instead of an LLM
estimating a price, national customs authorities report what was actually
traded, and the unit value (declared value / declared weight) IS the price.
Every figure returned here carries the live URL it came from, so any number
shown in the UI can be clicked through to its source.

Public preview endpoint -- no API key, no login. Responses are cached on disk
because the endpoint is aggressively rate-limited (HTTP 429).
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from typing import Any

BASE = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"

# HS 2209 -- "Vinegar and substitutes for vinegar obtained from acetic acid".
# A production version would resolve this per market; the demo is vinegar-only.
DEFAULT_HS = "2209"

# Comtrade numeric reporter/partner codes for the demo's country list.
COUNTRY_CODES: dict[str, int] = {
    "India": 699, "China": 156, "Japan": 392, "South Korea": 410, "Australia": 36,
    "U.S.": 842, "Canada": 124, "Mexico": 484,
    "Germany": 276, "U.K.": 826, "France": 250, "Italy": 380, "Spain": 724,
    "Brazil": 76, "Argentina": 32, "South Africa": 710, "Turkey": 792,
}
CODE_TO_NAME = {v: k for k, v in COUNTRY_CODES.items()}

# Vinegar density ~1.01 kg/L -- customs report weight, markets quote litres.
KG_PER_LITRE = 1.01

_CACHE_DIR = Path("output") / "trade_evidence"


def _cache_path(key: str, root: Path | None = None) -> Path:
    base = (Path(root) / _CACHE_DIR) if root else _CACHE_DIR
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{key}.json"


def _fetch(key: str, params: dict[str, Any], root: Path | None = None, retries: int = 3) -> dict[str, Any]:
    path = _cache_path(key, root)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    url = f"{BASE}?" + "&".join(f"{k}={v}" for k, v in params.items())
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            data = json.loads(urllib.request.urlopen(req, timeout=45).read())
            data["_source_url"] = url
            path.write_text(json.dumps(data), encoding="utf-8")
            return data
        except Exception as exc:  # noqa: BLE001 -- rate limits are expected
            last = exc
            time.sleep(8 * (attempt + 1))
    raise RuntimeError(f"Comtrade fetch failed for {key}: {last}")


def _agg(records: list[dict[str, Any]]) -> tuple[float, float]:
    """Comtrade splits one partner total across customs/transport-mode rows."""
    value = sum(float(r.get("primaryValue") or 0) for r in records)
    weight = sum(float(r.get("netWgt") or 0) for r in records)
    return value, weight


def fetch_country_trade_evidence(
    country: str, year: int = 2023, hs_code: str = DEFAULT_HS, root: Path | None = None,
) -> dict[str, Any]:
    """Imports, exports and per-origin unit values for one country -- each with
    the live source URL it came from. Raises only if the country isn't mapped;
    a failed network call degrades to `available: False` so the UI can show an
    honest 'no evidence retrieved' state rather than a fabricated number."""
    code = COUNTRY_CODES.get(country)
    if code is None:
        return {"available": False, "reason": f"No Comtrade reporter code mapped for '{country}'."}

    slug = country.lower().replace(".", "").replace(" ", "-")
    base_params = {"reporterCode": code, "period": year, "cmdCode": hs_code}
    try:
        imp = _fetch(f"{slug}_{year}_imp_world", {**base_params, "flowCode": "M", "partnerCode": 0}, root)
        exp = _fetch(f"{slug}_{year}_exp_world", {**base_params, "flowCode": "X", "partnerCode": 0}, root)
        par = _fetch(f"{slug}_{year}_imp_partners", {**base_params, "flowCode": "M"}, root)
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": str(exc)}

    imp_v, imp_kg = _agg(imp.get("data", []))
    exp_v, exp_kg = _agg(exp.get("data", []))

    by_partner: dict[int, list[float]] = {}
    for r in par.get("data", []):
        pc = r.get("partnerCode")
        if pc in (0, None):
            continue
        cur = by_partner.setdefault(int(pc), [0.0, 0.0])
        cur[0] += float(r.get("primaryValue") or 0)
        cur[1] += float(r.get("netWgt") or 0)

    origins = []
    for pc, (v, w) in by_partner.items():
        if w <= 0 or v <= 0:
            continue
        origins.append({
            "partner": CODE_TO_NAME.get(pc, str(pc)),
            "value_usd": round(v, 2),
            "weight_kg": round(w, 1),
            "unit_value_usd_per_kg": round(v / w, 3),
            "unit_value_usd_per_l": round(v / w * KG_PER_LITRE, 3),
        })
    origins.sort(key=lambda o: -o["value_usd"])

    return {
        "available": True,
        "country": country,
        "year": year,
        "hs_code": hs_code,
        "hs_description": "Vinegar and substitutes for vinegar obtained from acetic acid",
        "source": "UN Comtrade (official customs-reported trade statistics)",
        "source_urls": {
            "imports": imp.get("_source_url", ""),
            "exports": exp.get("_source_url", ""),
            "by_partner": par.get("_source_url", ""),
        },
        "kg_per_litre": KG_PER_LITRE,
        "imports": {
            "value_usd": round(imp_v, 2), "weight_kg": round(imp_kg, 1),
            "unit_value_usd_per_kg": round(imp_v / imp_kg, 3) if imp_kg else None,
            "unit_value_usd_per_l": round(imp_v / imp_kg * KG_PER_LITRE, 3) if imp_kg else None,
        },
        "exports": {
            "value_usd": round(exp_v, 2), "weight_kg": round(exp_kg, 1),
            "unit_value_usd_per_kg": round(exp_v / exp_kg, 3) if exp_kg else None,
            "unit_value_usd_per_l": round(exp_v / exp_kg * KG_PER_LITRE, 3) if exp_kg else None,
        },
        # Net trade only. Domestic production is NOT in customs data, so this
        # is deliberately not presented as market size -- see `coverage_note`.
        "net_trade_kg": round(imp_kg - exp_kg, 1),
        "origins": origins[:10],
        "coverage_note": (
            "Customs data covers imported and exported volume only. Domestically produced volume is not "
            "captured by any trade record and must be modelled separately -- it is never reported here as "
            "verified."
        ),
    }
