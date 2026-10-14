"""Build the number ledger for a 2A run from its sizing state and segment
report (`src/newinfo/system.md` §4, §6; `documentation.md` A.5).

Chain written per country:

    raw fact (FAOSTAT / Comtrade / web, with source + tier + quote)
      -> base-year quantity (derived when projected; leaf when the engine had it directly)
      -> price (customs unit value, curve, or the engine's price)
      -> value = quantity x price            (computed here, checked against the engine)
    countries -> regions -> global           (computed here, checked against the engine)
    country value x segment share -> segment value -> global segment value

Labels follow the Verifier rule (system.md §5): a number found in a tier 5
source (generic web) is `source_found_unverified`, never `verified_observed`.
"""
from __future__ import annotations

import re
from typing import Any

from .ledger import Ledger, Number
from .price_curve import tonnes_factor
from .topdown.regions import region_for

_FAO_RAW = re.compile(r",\s*(\d{4}):\s*([\d,]+(?:\.\d+)?)\s*t\b")
_REST = re.compile(r"^rest of (the )?world$", re.IGNORECASE)
FBS_STOCK_NOTE = ("FAOSTAT Food Balances 'Domestic supply quantity' = production + imports - exports + stock "
                  "withdrawals, so the stock change is already netted out (documentation.md B.2 #4)")


def source_tier(url: str, source: str = "") -> int:
    s = f"{url} {source}".lower()
    if "fao.org" in s or "faostat" in s:
        return 2
    if "comtrade" in s or "worldbank.org" in s:
        return 3
    if any(k in s for k in ("internationaloliveoil", "usda.gov", "eurostat", "ioc")):
        return 4
    if any(k in s for k in (".gov", "statistic", "census", "ine.es", "istat")):
        return 1
    return 5


def leaf_label(engine_label: str, tier: int) -> tuple[str, str | None]:
    """(label, weakest_input_label for derived leaves)."""
    lab = engine_label or "assumption"
    if lab == "verified_observed" and tier >= 5:
        return "source_found_unverified", None
    if lab == "derived":
        # derived by an upstream module from traceable text it did not ledger
        return "derived", "verified_observed" if tier <= 4 else "source_found_unverified"
    if lab not in ("verified_observed", "source_found_unverified", "estimated_from_facts", "assumption"):
        return "assumption", None
    return lab, None


def _quantity(led: Ledger, c: str, qty: dict[str, Any], raw: dict[str, Any], unit: str, base_year: int,
              world_raw: dict[str, Number]) -> Number:
    src = str(raw.get("source") or "")
    url = str(raw.get("source_url") or "")
    cite = str(raw.get("citation") or qty.get("note") or "")
    tier = source_tier(url, src)
    m = _FAO_RAW.search(cite)
    if m and tier == 2:
        year, value = int(m.group(1)), float(m.group(2).replace(",", ""))
        is_rest = bool(_REST.match(c.strip()))
        key = "World" if is_rest else c
        raw_n = world_raw.get(key) if is_rest else None
        if raw_n is None:
            raw_n = led.fact(f"{src} raw, {key}, {year}", value, unit, "global" if is_rest else c, "QTY",
                             "verified_observed", year=year, module="topdown.official_data",
                             source={"publisher": src, "url": url, "tier": tier, "quote": cite.split(";")[0]},
                             note=FBS_STOCK_NOTE)
            if is_rest:
                world_raw[key] = raw_n
        if year >= base_year and not is_rest:
            return raw_n
        formula = ("World projection − Σ listed countries' projections" if is_rest else
                   "mean(last 3 data years) × (1 + own trend)^(base year − centre year)")
        return led._derived(f"Quantity, {c}", float(qty["value"]), unit, c, "QTY", formula, [raw_n],
                            year=base_year, module="topdown.official_data", note=cite)
    label, weak = leaf_label(str(qty.get("provenance") or raw.get("evidence_type") or ""), tier)
    note = cite or str(qty.get("note") or "")
    if label == "assumption" and not note:
        note = "no source found by the sizing engine; engine estimate"
    return led.fact(f"Quantity, {c}", float(qty["value"]), unit, c, "QTY", label, year=raw.get("year") or base_year,
                    module="engine.demand_quantification", weakest_input_label=weak, note=note,
                    source={"publisher": src, "url": url, "tier": tier, "quote": cite[:300]} if (src or url) else None)


def _price(led: Ledger, c: str, state: dict[str, Any], bucket: str, unit: str, base_year: int,
           shared: dict[str, Number]) -> tuple[Number, str]:
    """(price row in USD per `unit`, value basis)."""
    table = state.get("country_prices")
    if table and c in table["prices"]:
        p = table["prices"][c]
        f = tonnes_factor(unit) or 1.0
        usd_per_unit = p["usd_per_t"] * f
        if p["method"].startswith("customs"):
            return led.fact(f"Price, {c}", usd_per_unit, f"USD/{unit}", c, "PRC", "verified_observed",
                            year=p.get("year"), module="price_curve",
                            source={"publisher": "UN Comtrade", "url": p.get("source_url", ""), "tier": 3,
                                    "quote": p["quote"]},
                            note=f"{table['basis_note']}; customs year {p.get('year')} applied to base year "
                                 f"{base_year} without price indexation"), table["basis"]
        if p["method"] == "curve":
            curve = shared.get("curve")
            if curve is None:
                cv = table["curve"]
                curve = shared["curve"] = led.fact(
                    "Price curve slope b (ln P on ln GDP pc PPP)", cv["b"], "dimensionless", "global", "PAR",
                    "derived", module="price_curve", weakest_input_label="verified_observed",
                    note=f"fitted on {cv['n']} customs import unit values; a = {cv['a']:.4f}; rmse(log) "
                         f"{cv['rmse_log']:.3f}")
            gdp = led.fact(f"GDP per capita PPP, {c}", p["gdp_pc_ppp"], "USD", c, "PAR", "verified_observed",
                           year=p.get("year"), module="price_curve",
                           source={"publisher": "World Bank", "url": (table.get("gdp_pc_ppp", {}).get(
                               table["iso3"].get(c, ""), {}) or {}).get("url", ""), "tier": 3,
                               "quote": f"NY.GDP.PCAP.PP.CD {p.get('year')}: {p['gdp_pc_ppp']:,.0f}"})
            return led._derived(f"Price, {c}", usd_per_unit, f"USD/{unit}", c, "PRC",
                                f"exp(a) × {gdp.number_id}^{curve.number_id}", [curve, gdp], year=base_year,
                                module="price_curve", note=p["quote"]), table["basis"]
        observed = led.find(kind="PRC")
        inputs = [n for n in observed if n.label == "verified_observed"] or observed[:1]
        return led._derived(f"Price, {c}", usd_per_unit, f"USD/{unit}", c, "PRC", "median(observed customs prices)",
                            inputs, year=base_year, module="price_curve", note=p["quote"]), table["basis"]
    # engine's single price for the bucket
    if "engine" not in shared:
        price = (state.get("prices") or {}).get(bucket) or {}
        norm = price.get("price_normalization") or {}
        url = str(norm.get("source_url") or "")
        tier = source_tier(url, str(norm.get("source") or ""))
        label, weak = leaf_label(str(price.get("evidence_type") or "assumption"), tier)
        shared["engine"] = led.fact(
            "Price (engine, all countries)", float(price.get("asp_usd_per_unit") or 0.0), f"USD/{unit}", "global",
            "PRC", label, year=norm.get("year"), module="engine.pricing", weakest_input_label=weak,
            source={"publisher": norm.get("source"), "url": url, "tier": tier,
                    "quote": str(price.get("rationale") or "")[:300]} if url else None,
            note=str(price.get("rationale") or "engine price with no verified source")[:300])
    return shared["engine"], "manufacturer"


def build_2a_ledger(state: dict[str, Any], run_id: str, pass_id: int | None = None,
                    report: dict[str, Any] | None = None) -> tuple[Ledger, dict[str, Any]]:
    """(ledger, facts used by checks: per-country prices, bases, per-capita)."""
    led = Ledger(run_id, "2A", pass_id)
    if not state.get("drivers"):
        return led, {"prices": {}, "bases": {}, "per_capita_kg": {}, "segment_globals": {}, "unit": "", "global": None}
    bucket = next(iter(state["drivers"]))
    driver = state["drivers"][bucket]
    unit = driver.get("final_demand_unit") or driver.get("unit") or ""
    base_year = int(state.get("base_year") or 2025)
    quantities = (state.get("quantities") or {}).get(bucket) or {}
    facts = (state.get("driver_facts") or {}).get(bucket) or {}
    engine_values = (state.get("usage_values") or {}).get(bucket) or {}
    shared: dict[str, Number] = {}
    world_raw: dict[str, Number] = {}
    country_val: dict[str, Number] = {}
    prices: dict[str, float] = {}
    bases: dict[str, str] = {}
    qty_rows: dict[str, Number] = {}
    for c, q in quantities.items():
        if not isinstance(q.get("value"), (int, float)):
            continue
        raw = (facts.get(c) or {}).get("driver_raw") or {}
        qn = _quantity(led, c, q, raw, unit, base_year, world_raw)
        qty_rows[c] = qn
        pn, basis = _price(led, c, state, bucket, unit, base_year, shared)
        prices[c], bases[c] = pn.value, basis
        vn = led.mul(f"Value, {c}", qn, pn, "USD", c, year=base_year)
        led.check(vn, float(engine_values.get(c, 0.0)) * 1e6, f"value {c}", rel_tol=1e-3)
        country_val[c] = vn

    regions: dict[str, list[Number]] = {}
    for c, vn in country_val.items():
        regions.setdefault(region_for(c), []).append(vn)
    engine_regions = state.get("region_totals") or {}
    region_rows = []
    for r, parts in regions.items():
        rn = led.total(f"Value, region {r}", parts, "USD", r, year=base_year)
        if r in engine_regions:
            led.check(rn, float(engine_regions[r]) * 1e6, f"region {r}", rel_tol=1e-3)
        region_rows.append(rn)
    glob = led.total("Value, global", region_rows, "USD", "global", year=base_year) if region_rows else None
    if glob is not None:
        led.check(glob, float(state.get("total_market_value") or 0.0) * 1e6, "global total", rel_tol=1e-3)

    segment_globals: dict[str, dict[str, float]] = {}
    for t in (report or {}).get("segmentation") or []:
        dim = t.get("dimension", "")
        status = str(t.get("evidence_status") or "")
        share_label = "source_found_unverified" if "evidence" in status else "assumption"
        reason = str(t.get("reasoning") or "LLM-estimated share from web snippets")[:300]
        per_segment: dict[str, list[Number]] = {}
        for c, shares in (t.get("shares") or {}).items():
            vn = country_val.get(c)
            if vn is None:
                continue
            for seg, sh in shares.items():
                sn = led.fact(f"Share {dim} / {seg}, {c}", float(sh), "share", c, "SHR", share_label,
                              year=base_year, module="topdown.segments", note=reason)
                sv = led.mul(f"Segment value {dim} / {seg}, {c}", vn, sn, "USD", c, year=base_year)
                led.check(sv, float(((t.get("country_values") or {}).get(c) or {}).get(seg, 0.0)) * 1e6,
                          f"segment {dim}/{seg} {c}", rel_tol=1e-3)
                per_segment.setdefault(seg, []).append(sv)
        segment_globals[dim] = {}
        for seg, parts in per_segment.items():
            gn = led.total(f"Segment value {dim} / {seg}, global", parts, "USD", "global", year=base_year)
            segment_globals[dim][seg] = gn.value / 1e6

    per_capita: dict[str, float] = {}
    table = state.get("country_prices") or {}
    pop = table.get("population") or {}
    f = tonnes_factor(unit)
    for c, qn in qty_rows.items():
        p = (pop.get((table.get("iso3") or {}).get(c, "")) or {}).get("value")
        if p and f is not None:
            per_capita[c] = qn.value * f * 1000.0 / p  # kg per person per year
    return led, {"prices": prices, "bases": bases, "per_capita_kg": per_capita, "segment_globals": segment_globals,
                 "unit": unit, "global": glob}
