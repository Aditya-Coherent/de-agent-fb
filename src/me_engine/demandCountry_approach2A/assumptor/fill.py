"""Replace the sizing engine's bare assumptions with assumptor estimates.

For the single 2A bucket, every country whose quantity is an `assumption`
(no source found, e.g. Japan for olive oil, which FAOSTAT does not list) is
re-estimated by the assumptor from the countries where the quantity IS known:

  - official-statistics driver (FAOSTAT): every FAOSTAT country for that item
    (~170 peers, latest 3-year mean) -- far richer than this run's countries;
  - otherwise: this run's own non-assumption countries.

The cell becomes `estimated_from_facts` with the assumptor's reason, 80%
interval and track record; its value is rescaled in proportion. Rest of
World is untouched (a FAOSTAT residual excludes countries FAOSTAT lacks).
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Callable

from .agent import assume
from .covariates import Covariates
from .policy import Policy

_REST = re.compile(r"^rest of (the )?world$", re.IGNORECASE)
_DOMAINS = {"fbs": "food", "rfb": "fertilizer"}
_MASS = {"tonnes": 1.0, "tonne": 1.0, "t": 1.0, "metric tons": 1.0, "kg": 1e-3}


def _official_peers(driver: dict[str, Any], cov: Covariates) -> tuple[dict[str, float], str] | None:
    src = driver.get("official_source") or {}
    if not src.get("dataset") or not src.get("item"):
        return None
    try:
        from ..topdown import official_data as od
        areas = (od.load_cache().get(src["dataset"]) or {}).get(src["item"]) or {}
    except Exception:  # noqa: BLE001
        return None
    known = {}
    for area, series in areas.items():
        pts = [v for _, v in sorted(series.items(), key=lambda kv: int(kv[0])) if v and v > 0][-3:]
        if pts and area.lower() != "world" and cov.get(area):
            known[area] = sum(pts) / len(pts)
    return (known, _DOMAINS.get(src["dataset"], "generic")) if known else None


def fill_assumptions(state: dict[str, Any], market: str, llm: Any | None = None, root: Path | None = None,
                     ceiling_kg_per_capita: float | None = None, say: Callable[[str], None] = print) -> dict[str, Any]:
    bucket = next(iter(state.get("drivers") or {}), None)
    if bucket is None:
        return state
    driver = state["drivers"][bucket]
    quantities = (state.get("quantities") or {}).get(bucket) or {}
    targets = [c for c, q in quantities.items() if not _REST.match(c.strip())
               and str(q.get("provenance") or q.get("evidence_type") or "") == "assumption"]
    if not targets:
        return state
    try:
        cov = Covariates.load(root)
    except Exception as exc:  # noqa: BLE001 -- no covariates: keep the engine's assumptions
        say(f"[assumptor] skipped: {exc}")
        return state
    policy = Policy.load(root)
    unit = driver.get("final_demand_unit") or driver.get("unit") or ""
    to_t = _MASS.get(unit.strip().lower())
    official = _official_peers(driver, cov) if to_t else None
    if official:
        known, domain = official
        known = {c: v / to_t for c, v in known.items()}  # FAOSTAT tonnes -> the run's unit
        basis = f"{len(known)} FAOSTAT countries"
    else:
        known = {c: float(q["value"]) for c, q in quantities.items() if c not in targets and not _REST.match(c.strip())
                 and isinstance(q.get("value"), (int, float)) and q["value"] > 0}
        domain, basis = "generic", f"{len(known)} countries of this run"
    display = ("kg", 1000.0 * to_t) if to_t else (None, 1.0)
    ceiling = ceiling_kg_per_capita / (1000.0 * to_t) if (ceiling_kg_per_capita and to_t) else None

    q_out = {**quantities}
    values = dict((state.get("usage_values") or {}).get(bucket) or {})
    log, filled = list(state.get("log") or []), {}
    for c in targets:
        est = assume(product=market, metric="annual consumption", unit=unit, country=c, known=known, cov=cov,
                     policy=policy, domain=domain, llm=llm, ceiling_per_capita=ceiling,
                     display_unit=display[0], display_scale=display[1])
        if not est:
            continue
        old = float(quantities[c].get("value") or 0.0)
        q_out[c] = {**quantities[c], "value": est["value"], "provenance": "estimated_from_facts",
                    "evidence_type": "estimated_from_facts", "engine_assumption": old,
                    "note": f"{est['reason']} (peers: {basis}; replaced the engine's assumption {old:,.0f} {unit})"}
        if old > 0 and c in values:
            values[c] = round(values[c] * est["value"] / old, 4)
        filled[c] = est
        msg = (f"[assumptor] {c}: {old:,.0f} -> {est['value']:,.0f} {unit} (80%: {est['p10']:,.0f}-{est['p90']:,.0f}; "
               f"{basis}; policy v{policy.version})")
        log.append(msg)
        say(msg)
    if not filled:
        return state
    out = dict(state)
    out["quantities"] = {**state["quantities"], bucket: q_out}
    out["usage_values"] = {**(state.get("usage_values") or {}), bucket: values}
    totals = dict(state.get("country_totals") or {})
    totals.update({c: values[c] for c in filled if c in values})
    out["country_totals"] = totals
    out["total_market_value"] = round(math.fsum(totals.values()), 4)
    out["assumptor"] = filled
    out["log"] = log
    return out
