"""Build a full Phase 1 plan without LLM when the API fails or returns bad JSON."""
from __future__ import annotations

import re
from typing import Any

from vendor_intel.funnel.discovery_prompts import build_discovery_prompts
from vendor_intel.funnel.query_intent import (
    build_generic_funnel_prompts,
    enrich_scope_from_query,
    infer_industry_context,
)
from vendor_intel.funnel.scope_schema import normalize_run_scope

# Universal value-chain roles (any B2B market)
_DEFAULT_ECOSYSTEM = [
    "manufacturers",
    "distributors",
    "wholesalers",
    "importers",
    "exporters",
    "retailers",
    "service providers",
    "consultants",
]

def offline_seed_companies(market: str, query: str) -> list[dict[str, str]]:
    """Known majors when LLM is down — industry inferred from query text only."""
    low = f"{market} {query}".lower()
    if any(k in low for k in ("pharma", "pharmaceutical", "medicine", "drug")):
        return [
            {"canonical_name": "Sun Pharmaceutical Industries", "primary_domain": "sunpharma.com", "company_function": "manufacturer"},
            {"canonical_name": "Cipla", "primary_domain": "cipla.com", "company_function": "manufacturer"},
            {"canonical_name": "Dr Reddys Laboratories", "primary_domain": "drreddys.com", "company_function": "manufacturer"},
            {"canonical_name": "Lupin", "primary_domain": "lupin.com", "company_function": "manufacturer"},
            {"canonical_name": "Aurobindo Pharma", "primary_domain": "aurobindo.com", "company_function": "manufacturer"},
            {"canonical_name": "Torrent Pharmaceuticals", "primary_domain": "torrentpharma.com", "company_function": "manufacturer"},
            {"canonical_name": "Divis Laboratories", "primary_domain": "divislabs.com", "company_function": "manufacturer"},
            {"canonical_name": "Cadila Healthcare", "primary_domain": "zyduscadila.com", "company_function": "manufacturer"},
            {"canonical_name": "Glenmark Pharmaceuticals", "primary_domain": "glenmarkpharma.com", "company_function": "manufacturer"},
            {"canonical_name": "Biocon", "primary_domain": "biocon.com", "company_function": "manufacturer"},
            {"canonical_name": "Alkem Laboratories", "primary_domain": "alkemlabs.com", "company_function": "manufacturer"},
            {"canonical_name": "Mankind Pharma", "primary_domain": "mankindpharma.com", "company_function": "manufacturer"},
        ]
    if any(k in low for k in ("atomic clock", "atomic timing", "frequency standard")):
        return [
            {"canonical_name": "Microchip Technology", "primary_domain": "microchip.com", "company_function": "manufacturer"},
            {"canonical_name": "Microsemi", "primary_domain": "microsemi.com", "company_function": "manufacturer"},
            {"canonical_name": "Safran", "primary_domain": "safran-group.com", "company_function": "manufacturer"},
            {"canonical_name": "Orolia", "primary_domain": "orolia.com", "company_function": "manufacturer"},
            {"canonical_name": "Spectratime", "primary_domain": "spectratime.com", "company_function": "manufacturer"},
            {"canonical_name": "Excelitas Technologies", "primary_domain": "excelitas.com", "company_function": "manufacturer"},
            {"canonical_name": "Stanford Research Systems", "primary_domain": "thinkSRS.com", "company_function": "manufacturer"},
            {"canonical_name": "Symmetricom", "primary_domain": "microsemi.com", "company_function": "manufacturer"},
        ]
    if any(k in low for k in ("g-code", "gcode", "cnc simulation", "nc simulation", "cam software")):
        return [
            {"canonical_name": "CGTech", "primary_domain": "cgtech.com", "company_function": "manufacturer"},
            {"canonical_name": "Siemens Digital Industries", "primary_domain": "sw.siemens.com", "company_function": "manufacturer"},
            {"canonical_name": "Autodesk", "primary_domain": "autodesk.com", "company_function": "manufacturer"},
            {"canonical_name": "Dassault Systemes", "primary_domain": "3ds.com", "company_function": "manufacturer"},
            {"canonical_name": "Hexagon Manufacturing Intelligence", "primary_domain": "hexagonmi.com", "company_function": "manufacturer"},
            {"canonical_name": "OPEN MIND Technologies", "primary_domain": "openmind-tech.com", "company_function": "manufacturer"},
            {"canonical_name": "ModuleWorks", "primary_domain": "moduleworks.com", "company_function": "manufacturer"},
            {"canonical_name": "NCSIMUL", "primary_domain": "sprutcam.com", "company_function": "manufacturer"},
        ]
    if any(k in low for k in ("wind", "distributed wind", "turbine", "renewable infrastructure")):
        return [
            {"canonical_name": "Vestas", "primary_domain": "vestas.com", "company_function": "manufacturer"},
            {"canonical_name": "Siemens Gamesa", "primary_domain": "siemensgamesa.com", "company_function": "manufacturer"},
            {"canonical_name": "GE Vernova", "primary_domain": "gevernova.com", "company_function": "manufacturer"},
            {"canonical_name": "Nordex", "primary_domain": "nordex-online.com", "company_function": "manufacturer"},
            {"canonical_name": "Goldwind", "primary_domain": "goldwind.com", "company_function": "manufacturer"},
            {"canonical_name": "Envision Energy", "primary_domain": "envision-group.com", "company_function": "manufacturer"},
            {"canonical_name": "Enercon", "primary_domain": "enercon.de", "company_function": "manufacturer"},
            {"canonical_name": "Mingyang Smart Energy", "primary_domain": "myse.com.cn", "company_function": "manufacturer"},
        ]
    return []


_GENERIC_NEGATIVE = [
    "wikipedia",
    "tutorial",
    "course",
    "jobs",
    "careers",
    "news",
    "blog",
    "directory",
    "market report",
    "stock price",
    "definition",
    "meaning",
]


def infer_ecosystem_functions(market: str, query: str) -> list[str]:
    """Infer roles from market/query wording only (no company name lists)."""
    low = f"{market} {query}".lower()
    if any(k in low for k in ("pharma", "pharmaceutical", "medicine", "drug", "api ")):
        return [
            "API manufacturers",
            "formulation manufacturers",
            "CDMO",
            "pharmaceutical distributors",
            "generics manufacturers",
            "biosimilar companies",
            "contract manufacturers",
            "pharma exporters",
        ]
    if any(k in low for k in ("cyber", "security", "infosec", "mssp", "siem")):
        return [
            "endpoint security vendors",
            "MSSP",
            "system integrators",
            "VARs",
            "security consultants",
            "distributors",
            "cloud security providers",
            "threat intelligence firms",
        ]
    if any(k in low for k in ("food", "fmcg", "snack", "beverage", "packaged")):
        return [
            "food manufacturers",
            "co-packers",
            "brand owners",
            "distributors",
            "wholesalers",
            "importers",
            "retailers",
            "exporters",
        ]
    if any(k in low for k in ("software", "saas", "it services", "cloud")):
        return [
            "software vendors",
            "SaaS companies",
            "system integrators",
            "IT services firms",
            "cloud providers",
            "resellers",
            "consulting firms",
            "distributors",
        ]
    if any(
        k in low
        for k in (
            "ethylene",
            "polymer",
            "petrochemical",
            "chemical",
            "bio-based",
            "biobased",
            "olefin",
            "plastics",
        )
    ):
        return [
            "ethylene producers",
            "petrochemical manufacturers",
            "polymer producers",
            "chemical distributors",
            "bio-based chemical companies",
            "plant operators",
            "commodity traders",
            "engineering contractors",
        ]
    if any(k in low for k in ("atomic clock", "atomic timing", "frequency standard", "cesium", "rubidium")):
        return [
            "atomic clock manufacturers",
            "frequency standard OEMs",
            "timing instrument suppliers",
            "GNSS timing integrators",
            "oscillator manufacturers",
            "precision timing distributors",
            "defense timing contractors",
            "calibration service providers",
        ]
    if any(k in low for k in ("g-code", "gcode", "cnc simulation", "nc simulation", "cam software", "machining simulation")):
        return [
            "CNC simulation software vendors",
            "CAM software developers",
            "machine tool OEMs",
            "post-processor providers",
            "CNC integrators",
            "manufacturing software distributors",
            "digital twin platform vendors",
            "machining technology suppliers",
        ]
    if any(k in low for k in ("wind", "turbine", "distributed energy", "renewable infrastructure", "microgrid")):
        return [
            "wind turbine manufacturers",
            "distributed wind developers",
            "renewable EPC contractors",
            "grid integration firms",
            "energy storage suppliers",
            "wind farm operators",
            "power electronics manufacturers",
            "renewable project developers",
        ]
    if any(
        k in low
        for k in ("digital signage", "signage system", "dooh", "video wall", "narrowcasting")
    ):
        return [
            "digital signage software vendors",
            "signage CMS providers",
            "media player manufacturers",
            "LED display manufacturers",
            "video wall system integrators",
            "DOOH platform providers",
            "interactive kiosk vendors",
            "content management for displays",
            "cloud signage platforms",
            "digital menu board software",
        ]
    if any(
        k in low
        for k in (
            "cladding",
            "facade",
            "façade",
            "curtain wall",
            "rainscreen",
            "composite panel",
            "building envelope",
            "acp",
        )
    ):
        return [
            "ACP panel manufacturers",
            "aluminium composite panel producers",
            "rainscreen cladding system houses",
            "curtain wall manufacturers",
            "façade system integrators",
            "ventilated facade suppliers",
            "building envelope specialists",
            "façade contractors",
            "aluminium cladding distributors",
            "facade fabricators",
        ]
    return list(_DEFAULT_ECOSYSTEM)


def _relevance_keywords(market: str, industry_terms: list[str], geo: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for src in (industry_terms or []) + re.findall(r"[a-z]{4,}", market.lower()):
        s = str(src).strip()
        if len(s) < 3:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        terms.append(s)
    if geo and geo.lower() not in ("", "global"):
        terms.append(geo.split(",")[0].strip())
    return terms[:12]


def build_offline_compiler_plan(
    query: str,
    scope: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]], list[dict[str, str]]]:
    """Full scope + funnel + discovery prompts with no LLM."""
    scope = enrich_scope_from_query(dict(scope or {}), query)
    market = str(scope.get("market") or "")
    geo = (scope.get("geographies") or ["global"])[0]
    ctx = infer_industry_context(market, query)

    if not scope.get("industry_vertical"):
        scope["industry_vertical"] = ctx["industry_vertical"]
    if not scope.get("industry_terms"):
        scope["industry_terms"] = ctx["industry_terms"]

    scope["ecosystem_functions"] = scope.get("ecosystem_functions") or infer_ecosystem_functions(
        market, query
    )
    scope["relevance_keywords"] = scope.get("relevance_keywords") or _relevance_keywords(
        market, scope.get("industry_terms") or [], geo
    )
    scope["negative_keywords"] = scope.get("negative_keywords") or list(_GENERIC_NEGATIVE)
    scope["company_type"] = scope.get("company_type") or "company"
    scope["scope_source"] = "offline_fallback"
    if not scope.get("seed_companies"):
        scope["seed_companies"] = offline_seed_companies(market, query)

    scope = normalize_run_scope(scope, query)
    funnel = build_generic_funnel_prompts(
        market, geo, industry_terms=scope.get("industry_terms")
    )
    from vendor_intel.pipeline.geo_limits import is_global_geography

    max_p = 20 if is_global_geography(str(geo)) else 12
    discovery = build_discovery_prompts(scope, query, funnel, max_prompts=max_p, llm_prompts=None)
    return scope, funnel, discovery
