"""Hard and soft checks (`src/newinfo/system.md` §11).

Hard failure -> nothing is written to the run's outputs; the run report lists
the failure. Soft flags only warn -- no check here ever edits a number.

| Check               | Severity | Rule (defaults in design_config.py)                    |
|---------------------|----------|--------------------------------------------------------|
| missing label / NaN / negative | hard | any ledger number                            |
| sum conservation    | hard     | ledger-derived roll-ups vs engine figures > 0.1%       |
| segment re-add      | hard     | every segment table re-adds to the country/global total |
| mix shares sum      | hard     | segment / type shares != 1.0 +/- 0.001                 |
| basis mixing        | hard     | more than one value basis in the run                   |
| magnitude           | soft     | per-capita outside [floor, ceiling]                    |
| triangulation       | soft     | |bottom-up - published median| / median > 25%         |
| 2A vs 2B gap        | soft     | ratio outside [1/1.5, 1.5] after anchoring             |
| use-case dominance  | soft     | one use case (application) > 50% of the market         |
| evidence coverage   | soft     | < 50% of value rests on verified inputs                |
| price outlier       | soft     | country price > 3x IQR from the median                 |
| price year          | soft     | customs price not from the base year                   |
| recipe coverage (2C)| soft     | see strategy2c.py                                      |
"""
from __future__ import annotations

import math
import statistics
from typing import Any

from .ledger import LABELS, Ledger, geo_code

Flag = dict[str, Any]


def flag(severity: str, check: str, message: str, scope: str = "", number_ids: list[str] | None = None) -> Flag:
    return {"severity": severity, "check": check, "scope": scope, "message": message, "number_ids": number_ids or []}


def has_hard(flags: list[Flag]) -> bool:
    return any(f["severity"] == "hard" for f in flags)


# --------------------------------------------------------------------------- #
# hard
# --------------------------------------------------------------------------- #
def ledger_integrity(ledger: Ledger) -> list[Flag]:
    out: list[Flag] = []
    for n in ledger.rows:
        if n.label not in LABELS:
            out.append(flag("hard", "missing_label", f"{n.number_id} has no valid label", n.geo, [n.number_id]))
        if n.value is None or math.isnan(n.value) or math.isinf(n.value):
            out.append(flag("hard", "nan_value", f"{n.number_id} is NaN/inf", n.geo, [n.number_id]))
        elif n.value < 0:
            out.append(flag("hard", "negative_value", f"{n.number_id} ({n.name}) is negative: {n.value:,.4f}",
                            n.geo, [n.number_id]))
    for m in ledger.mismatches:
        out.append(flag("hard", "sum_conservation",
                        f"{m['what']}: ledger {m['ledger']:,.4f} vs engine {m['engine']:,.4f}", "", [m["number_id"]]))
    return out


def segment_tables(report: dict[str, Any] | None, country_totals_usd_mn: dict[str, float],
                   tolerance: float = 0.001, share_tolerance: float = 0.001) -> list[Flag]:
    """Every dimension's shares sum to 1 per country, and every table re-adds
    to the same country totals and global total (relative tolerance)."""
    if not report:
        return []
    out: list[Flag] = []
    global_total = math.fsum(float(v or 0.0) for v in country_totals_usd_mn.values())
    for t in report.get("segmentation") or []:
        dim = t.get("dimension", "")
        for c, shares in (t.get("shares") or {}).items():
            s = math.fsum(float(v or 0.0) for v in shares.values())
            if abs(s - 1.0) > share_tolerance:
                out.append(flag("hard", "mix_shares_sum", f"{dim}, {c}: shares sum to {s:.4f}", geo_code(c)))
        for c, total in country_totals_usd_mn.items():
            seg = math.fsum((t.get("country_values") or {}).get(c, {}).values())
            if total and abs(seg - total) / total > tolerance:
                out.append(flag("hard", "segment_readd", f"{dim}, {c}: segments sum to {seg:,.3f} vs country "
                                f"total {total:,.3f}", geo_code(c)))
        g = float(t.get("global_total") or 0.0)
        if global_total and abs(g - global_total) / global_total > tolerance:
            out.append(flag("hard", "segment_readd", f"{dim}: global {g:,.3f} vs market {global_total:,.3f}", "WLD"))
    return out


def basis_mixing(price_bases: dict[str, str]) -> list[Flag]:
    """{country: value basis}; one basis per run."""
    bases = sorted({b for b in price_bases.values() if b})
    if len(bases) > 1:
        return [flag("hard", "basis_mixing", "more than one value basis in the run: " + ", ".join(
            f"{b} ({', '.join(c for c, x in price_bases.items() if x == b)})" for b in bases))]
    return []


# --------------------------------------------------------------------------- #
# soft
# --------------------------------------------------------------------------- #
def magnitude(per_capita: dict[str, float], band: dict[str, Any] | None) -> list[Flag]:
    """per_capita: {country: physical units per person per year, in band['unit']}."""
    if not band:
        return []
    out = []
    for c, v in per_capita.items():
        if not (band["floor"] <= v <= band["ceiling"]):
            out.append(flag("soft", "magnitude", f"{c}: {v:,.2f} {band['unit']}/person/yr outside "
                            f"[{band['floor']}, {band['ceiling']}]", geo_code(c)))
    return out


def triangulation(bottom_up_usd_mn: float, published_usd_mn: list[float], warn: float = 0.25) -> list[Flag]:
    if not published_usd_mn or bottom_up_usd_mn <= 0:
        return []
    med = statistics.median(published_usd_mn)
    gap = (bottom_up_usd_mn - med) / med
    if abs(gap) > warn:
        return [flag("soft", "triangulation", f"bottom-up ${bottom_up_usd_mn:,.0f}M is {gap:+.0%} from the "
                     f"published median ${med:,.0f}M (n={len(published_usd_mn)}) -- warning only, never used to "
                     "adjust", "WLD")]
    return []


def gap_2a_2b(value_2b: float, value_2a: float, band: float = 1.5) -> list[Flag]:
    if value_2a <= 0 or value_2b <= 0:
        return []
    r = value_2b / value_2a
    if not (1 / band <= r <= band):
        return [flag("soft", "gap_2a_2b", f"2B/2A = {r:.3f}, outside [{1 / band:.3f}, {band}] after anchoring", "WLD")]
    return []


def dominance(values: dict[str, float], threshold: float = 0.50, what: str = "use case") -> list[Flag]:
    total = math.fsum(values.values())
    if total <= 0:
        return []
    return [flag("soft", "use_case_dominance", f"{k} is {v / total:.1%} of the market (> {threshold:.0%} {what} "
                 "dominance threshold) -- needs a reviewer justification", "WLD")
            for k, v in values.items() if v / total > threshold]


def evidence_coverage(verified_share: float, minimum: float = 0.50) -> list[Flag]:
    if verified_share < minimum:
        return [flag("soft", "evidence_coverage", f"only {verified_share:.1%} of value rests on inputs whose weakest "
                     f"label is verified_observed (minimum {minimum:.0%})", "WLD")]
    return []


def price_outliers(prices: dict[str, float], iqr_mult: float = 3.0) -> list[Flag]:
    vals = sorted(v for v in prices.values() if v and v > 0)
    if len(vals) < 4:
        return []
    q1, med, q3 = statistics.quantiles(vals, n=4, method="inclusive")
    iqr = q3 - q1
    if iqr <= 0:
        return []
    return [flag("soft", "price_outlier", f"{c}: price {p:,.0f} is more than {iqr_mult:g}x IQR ({iqr:,.0f}) from the "
                 f"median {med:,.0f}", geo_code(c)) for c, p in prices.items() if p and abs(p - med) > iqr_mult * iqr]


def price_year(state: dict[str, Any]) -> list[Flag]:
    """Customs prices from a year other than the base year (system.md §10 unit traps)."""
    off = (state.get("country_prices") or {}).get("off_year_prices") or {}
    if not off:
        return []
    base = (state.get("country_prices") or {}).get("base_year")
    return [flag("soft", "price_year_mismatch", f"{len(off)} customs price(s) not from base year {base}: "
                 + ", ".join(f"{c} {y}" for c, y in off.items()) + " -- applied without price indexation", "WLD")]


def verified_value_share(ledger: Ledger) -> float:
    """Share of global value whose weakest input is verified_observed, over
    the country value rows (`VAL` rows named 'Value, <country>')."""
    rows = ledger.find(kind="VAL", name_prefix="Value, ")
    total = math.fsum(r.value for r in rows)
    if total <= 0:
        return 0.0
    return math.fsum(r.value for r in rows if r.weakest_input_label == "verified_observed") / total


def published_sizes(state: dict[str, Any]) -> list[float]:
    """Published market sizes (USD Mn) the sizing engine found -- context for
    the triangulation warning and the optional Publisher, never an input."""
    out = []
    for t in state.get("triangulation") or []:
        if str(t.get("unit") or "").lower().replace(" ", "") in ("usdmn", "usdmillion") and t.get("value"):
            out.append(float(t["value"]))
    return out


def _application_dimension(report: dict[str, Any] | None, segment_globals: dict[str, dict[str, float]]) -> dict[str, float]:
    for dim, vals in segment_globals.items():
        d = dim.lower()
        if "application" in d or "end use" in d or "end-use" in d or "use case" in d:
            return vals
    return {}


def evaluate(cfg: dict[str, Any], state: dict[str, Any], ledger: Ledger, facts: dict[str, Any],
             band: dict[str, Any] | None, report: dict[str, Any] | None = None) -> dict[str, Any]:
    """All checks for one 2A pass -> {hard, soft, verified_share}."""
    c = cfg["checks"]
    hard = ledger_integrity(ledger) + basis_mixing(facts.get("bases") or {})
    if report is not None:
        hard += segment_tables(report, {k: float(v or 0.0) for k, v in (state.get("country_totals") or {}).items()},
                               c["sum_tolerance"], c["share_tolerance"])
    share = verified_value_share(ledger)
    soft = (magnitude(facts.get("per_capita_kg") or {}, band if band and band.get("unit") == "kg" else None)
            + triangulation(float(state.get("total_market_value") or 0.0), published_sizes(state),
                            c["triangulation_warn"])
            + evidence_coverage(share, c["min_verified_coverage"])
            + price_outliers(facts.get("prices") or {}, c["price_outlier_iqr"])
            + price_year(state)
            + dominance(_application_dimension(report, facts.get("segment_globals") or {}), c["use_case_dominance"]))
    return {"hard": hard, "soft": soft, "verified_share": share}
