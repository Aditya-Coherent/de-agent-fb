"""Shared ranking rules (PLAN-07): when two values count as tied, and how the
MEA growth factors are tied to the CAGRs they claim to explain.

  * TIE_PP / TIE_REL -- two CAGRs within 0.3 pp, or two values within 3%,
    are "similar": no text may call one of them the fastest / largest.
    prose._rank, guard.ClaimGuard and audit.check_ties all use `tied()`.
  * reconcile_drivers -- the agents' driver / restraint impacts (pp) are
    rescaled so that  global CAGR + sum(drivers) + sum(restraints) = MEA CAGR,
    the baseline being the global CAGR. If the numbers don't allow that, the
    impacts become relative scores (1-5) with no pp.
"""
from __future__ import annotations

import re

TIE_PP = 0.3          # CAGRs within 0.3 percentage points are a tie
TIE_REL = 0.03        # values (sizes, increments) within 3% are a tie

# a rescaling factor outside this range distorts the agents' assessment too much: use scores instead
_MIN_SCALE, _MAX_SCALE = 0.25, 4.0


def tied(key: str, a: float, b: float) -> bool:
    """True when `a` and `b` are too close to rank: CAGRs ('cagr') within
    TIE_PP, anything else within TIE_REL of the larger value."""
    if key == "cagr":
        return abs(a - b) <= TIE_PP + 1e-9
    return abs(a - b) <= TIE_REL * max(abs(a), abs(b)) + 1e-9


def top_tied(d: dict, key: str, best: bool = True) -> list[str]:
    """The leader of `d` ({name: {key: value}}) and every peer tied with it."""
    order = sorted(d, key=lambda k: d[k][key], reverse=best)
    if not order:
        return []
    return [k for k in order if tied(key, d[k][key], d[order[0]][key])]


def _round_to(items: list[dict], total: float) -> None:
    """Rounds impacts to 0.1 pp so that the printed values add up to `total`
    exactly (largest-remainder rounding, which keeps the items' order)."""
    if not items:
        return
    sign = -1 if total < 0 else 1
    exact = [abs(x["impact_pp"]) * 10 for x in items]
    want = int(round(abs(total) * 10))
    tenths = [int(e) for e in exact]
    by_rest = sorted(range(len(items)), key=lambda i: exact[i] - tenths[i], reverse=True)
    for k in range(max(0, want - sum(tenths))):
        tenths[by_rest[k % len(items)]] += 1
    for x, t in zip(items, tenths):
        x["impact_pp"] = sign * t / 10


def reconcile_drivers(dyn: dict, global_cagr: float, mea_cagr: float) -> dict:
    """Ties the MEA drivers and restraints to the CAGRs (FINDINGS X15), in place.

    (a) Each side keeps its internal proportions and the total weight of all
        factors is kept; the balance between the two sides is set so that
        sum(drivers) + sum(restraints) = MEA CAGR - global CAGR. The agents'
        values are kept in `impact_pp_agent`.
    (b) When (a) would scale a side by less than 0.25x or more than 4x (or a
        side is empty), the impacts become relative scores 1-5 (`impact_score`)
        and `impact_pp` is removed.

    Idempotent: a block already reconciled is returned unchanged."""
    if not dyn or dyn.get("impact_basis"):
        return dyn
    drv = [x for x in dyn.get("mea_drivers") or [] if x.get("impact_pp") is not None]
    rst = [x for x in dyn.get("mea_restraints") or [] if x.get("impact_pp") is not None]
    if not drv and not rst:
        return dyn
    for x in drv + rst:
        x["impact_pp_agent"] = float(x["impact_pp"])
    d = sum(abs(x["impact_pp_agent"]) for x in drv)
    r = sum(abs(x["impact_pp_agent"]) for x in rst)
    target = round(mea_cagr, 1) - round(global_cagr, 1)        # as printed: 16.9% - 22.3% = -5.4 pp
    s = d + r
    d_new, r_new = (s + target) / 2, (s - target) / 2
    ok = d > 0 and r > 0 and d_new > 0 and r_new > 0 and \
        _MIN_SCALE <= d_new / d <= _MAX_SCALE and _MIN_SCALE <= r_new / r <= _MAX_SCALE
    if ok:
        for x in drv:
            x["impact_pp"] = abs(x["impact_pp_agent"]) * d_new / d
        for x in rst:
            x["impact_pp"] = -abs(x["impact_pp_agent"]) * r_new / r
        _round_to(drv, round(d_new, 1))
        _round_to(rst, round(target - round(d_new, 1), 1))       # restraints close the gap to the target
        dyn["impact_basis"] = "pp_vs_global"
        dyn["baseline_cagr_pct"] = round(global_cagr, 1)
        dyn["mea_cagr_pct"] = round(mea_cagr, 1)
        return dyn
    # (b) relative scores: 5 = the largest absolute impact
    top = max(abs(x["impact_pp_agent"]) for x in drv + rst) or 1.0
    for x in drv + rst:
        sc = max(1, min(5, round(abs(x["impact_pp_agent"]) / top * 5)))
        x["impact_score"] = sc if x["impact_pp_agent"] >= 0 else -sc
        x.pop("impact_pp", None)
    dyn["impact_basis"] = "score"
    return dyn


def impact_label(factor: str, dyn: dict) -> str | None:
    """Table 2's High/Medium/Low for a factor, derived from the same impacts the report prints
    (a 'Low' restraint can no longer carry the largest pp): the factor's matching driver or
    restraint, relative to the largest absolute impact -- >= 60% High, >= 30% Medium, else Low.
    None when the factor matches no driver or restraint (the agent's label is kept)."""
    items = [x for x in (dyn.get("mea_drivers") or []) + (dyn.get("mea_restraints") or [])]
    val = (lambda x: abs(float(x.get("impact_pp", x.get("impact_score", 0)) or 0)))
    top = max((val(x) for x in items), default=0)
    if not top:
        return None
    fw = {w for w in re.findall(r"[a-z]+", factor.lower()) if len(w) > 3}
    best, score = None, 0.0
    for x in items:
        xw = {w for w in re.findall(r"[a-z]+", str(x.get("name", "")).lower()) if len(w) > 3}
        ov = len(fw & xw) / max(1, min(len(fw), len(xw)))
        if ov > score:
            best, score = x, ov
    if not best or score < 0.5:
        return None
    r = val(best) / top
    return "High" if r >= 0.6 else "Medium" if r >= 0.3 else "Low"
