"""Runs AFTER randomization and BEFORE the report is built. Each field group
is synthesized/jittered independently, so nothing guarantees the numbers
agree with each other (regions summing past the global total, a scenario
"base case" that doesn't match the headline forecast, Porter's labels that
don't match their own score, etc.) -- this module is the single place that
makes every number in `whitepaper_fields.json` internally consistent before
anything is rendered.

Two entry points:
    reconcile(fields, market_name) -> (fixed_fields, changes)
        Derives everything from the base-year value and CAGR (the only two
        numbers treated as ground truth) and rescales/recomputes every other
        group to agree with them. `changes` is a dict of group-key ->
        [human-readable change descriptions], used both for the run log and
        for computing each section's confidence label (a group the
        consistency pass had to adjust heavily is less trustworthy).
    validate(fields, market_name) -> [issue, ...]
        Read-only: returns a list of human-readable problems found. Run
        BEFORE reconcile (the "before" report) and AFTER reconcile (the
        "after" report, which should be empty or near-empty).

No LLM calls here -- this is pure, deterministic arithmetic.
"""
from __future__ import annotations

import copy
import re
from typing import Any

FIXED_REGIONS = ("North America", "Europe", "Asia Pacific", "Latin America", "Middle East & Africa")
_SHARE_TOLERANCE = 0.5
_REGION_SUM_TOLERANCE_PCT = 1.0
_CAGR_TOLERANCE_PP = 0.2
_SCOPE_BLEED_MULTIPLE = 3.0

_LEAKED_TITLE_RE = re.compile(
    r"\(\s*20\d{2}.*?\)|\bprojection\b|\breport\b|\bforecast to\b|\bmarket\s+size\b|\bwhitepaper\b|\bmarket\s*$", re.I,
)

# Geography words that, as the FIRST word of a derivative/sub-segment name,
# mean the entry is a regional slice of a market ("Europe Poultry Feed",
# "India Poultry Market") rather than a distinct product.
_GEO_PREFIXES = {
    "global", "north", "europe", "european", "asia", "asian", "latin", "middle", "africa", "african",
    "india", "indian", "china", "chinese", "us", "u.s.", "usa", "united", "japan", "brazil",
}
_SCENARIO_MIN_GAP_PP = 1.5
_SCENARIO_MIN_GAP_REL = 0.20
_MAX_RESTRAINT_TO_DRIVER_RATIO = 0.30


# --------------------------------------------------------------------------- #
# small shared helpers
# --------------------------------------------------------------------------- #
def _normalize_shares(items: list[dict], key: str) -> float:
    """Rescales `items[*][key]` in place so they sum to exactly 100. Returns
    the original sum (for change-logging). No-ops on an empty list."""
    if not items:
        return 100.0
    total = sum(float(it.get(key, 0) or 0) for it in items)
    if total <= 0:
        even = 100.0 / len(items)
        for it in items:
            it[key] = round(even, 2)
        return total
    factor = 100.0 / total
    for it in items:
        it[key] = round(float(it.get(key, 0) or 0) * factor, 2)
    return total


def looks_like_leaked_search_title(name: str) -> bool:
    """True if a segment/derivative NAME looks like a leaked search-result
    title (e.g. "Biostimulants Market (2031 projection)") rather than an
    actual product/segment name."""
    if not name:
        return False
    return bool(_LEAKED_TITLE_RE.search(name))


def _market_root_words(market_name: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", market_name.lower()) if w not in {"global", "market"}]


def looks_like_parent_or_regional_market(name: str, market_name: str) -> bool:
    """True if a derivative/sub-segment NAME is really the parent market
    ("Poultry Feed" inside "Functional Poultry Feed") or a regional slice of
    a market ("Europe Poultry Feed") -- neither is a distinct derivative."""
    words = re.findall(r"[a-z0-9.]+", (name or "").lower())
    if not words:
        return False
    if words[0] in _GEO_PREFIXES:
        return True
    root = _market_root_words(market_name)
    core = [w for w in words if w != "market"]
    return bool(core) and len(core) < len(root) and root[-len(core):] == core


_GENERIC_SCOPE_WORDS = {
    "and", "or", "the", "of", "for", "with", "without", "that", "only", "a", "an", "other", "others",
    "products", "product", "systems", "system", "market", "markets",
}


def exclusion_terms(definition: dict | None, market_name: str) -> set[str]:
    """Distinctive 6-char stems of the excluded categories in a market
    definition -- the first meaningful word of each comma/semicolon/slash
    separated exclusion phrase, skipping words that are part of the market's
    own name (e.g. "Traditional/extensive shrimp farming, shrimp hatcheries"
    -> {"tradit", "extens", "hatche"} for a shrimp-farming market)."""
    if not definition:
        return set()
    text = str(definition.get("exclusions") or definition.get("excluded") or "")
    own = {w[:6] for w in _market_root_words(market_name)}
    terms: set[str] = set()
    for phrase in re.split(r"[,;/]|\band\b", text, flags=re.I):
        for w in re.findall(r"[a-z][a-z\-]+", phrase.lower()):
            if w in _GENERIC_SCOPE_WORDS or w[:6] in own:
                continue
            terms.add(w[:6])
            break
    return terms


def _is_excluded_by_definition(name: str, terms: set[str]) -> bool:
    return any(w[:6] in terms for w in re.findall(r"[a-z][a-z\-]+", (name or "").lower()))


def _drop_out_of_scope(items: list[dict], terms: set[str], group: str, changes: list[str]) -> list[dict]:
    """Drops share-based segments the market definition explicitly excludes
    (e.g. "Pond-based (extensive & semi-intensive) farming" inside a
    super-intensive market). Never drops every item -- if everything would
    go, the list is kept as-is and left for a scoped re-run to fix."""
    if not terms or not items:
        return items
    kept = [it for it in items if not _is_excluded_by_definition(str(it.get("name", "")), terms)]
    if not kept or len(kept) == len(items):
        return items
    for it in items:
        if it not in kept:
            changes.append(f"{group}: dropped '{it.get('name')}' (excluded by the market definition)")
    return kept


def porter_label(score: float) -> str:
    if score <= 2.0:
        return "Low"
    if score < 3.5:
        return "Medium"
    return "High"


def concentration_label(top3_share_pct: float) -> str:
    if top3_share_pct < 25:
        return "fragmented"
    if top3_share_pct <= 50:
        return "moderately concentrated"
    return "highly concentrated"


# --------------------------------------------------------------------------- #
# reconcile() -- one sub-function per field group
# --------------------------------------------------------------------------- #
def _reconcile_market_size(ms: dict, changes: list[str]) -> tuple[dict, float, float, int, int]:
    base_year = int(ms.get("base_year", 2025) or 2025)
    forecast_year = int(ms.get("forecast_year", base_year + 10) or base_year + 10)
    base_val = float(ms.get("base_year_value_usd_mn", 0) or 0)
    cagr_pct = float(ms.get("cagr_pct", 0) or 0)
    years = max(1, forecast_year - base_year)
    rate = cagr_pct / 100.0

    new_forecast_val = round(base_val * (1 + rate) ** years, 2)
    # any change is listed, so fields.market_size differs from fields_raw only where consistency_changes says
    if abs(new_forecast_val - float(ms.get("forecast_value_usd_mn", 0) or 0)) > 0.005:
        changes.append(
            f"forecast_value_usd_mn recomputed from base*({1+rate:.4f})^{years} "
            f"({ms.get('forecast_value_usd_mn')} -> {new_forecast_val})"
        )
    ms["forecast_value_usd_mn"] = new_forecast_val

    new_series = {str(y): round(base_val * (1 + rate) ** (y - base_year), 2) for y in range(base_year, forecast_year + 1)}
    if ms.get("forecast_series_usd_mn") != new_series:
        changes.append("forecast_series_usd_mn rebuilt by compounding from base_year_value_usd_mn at cagr_pct (now strictly non-decreasing)")
    ms["forecast_series_usd_mn"] = new_series

    hist = dict(ms.get("historical_usd_mn") or {})
    if hist:
        hyears = sorted(int(y) for y in hist)
        last_val = hist[str(hyears[-1])] or 1
        gap = base_year - hyears[-1]
        target_last = base_val / ((1 + rate) ** max(gap, 0)) if gap > 0 else base_val
        factor = target_last / last_val if last_val else 1
        if abs(factor - 1) <= 0.02:
            factor = 1.0                # already leads into the base: keep the raw values
        new_hist = {str(y): round(hist[str(y)] * factor, 2) for y in hyears}
        # History is printed only if it passes: (1) for a growing market it never
        # falls year on year, into the base year included; (2) no year-on-year move
        # is more than 2x the CAGR (a jump like +30% vs a 12% CAGR is noise).
        series = [(y, new_hist[str(y)]) for y in hyears] + ([(base_year, base_val)] if gap > 0 else [])
        warnings: list[str] = []
        if rate >= 0:
            dips = [f"{y1}->{y2}" for (y1, v1), (y2, v2) in zip(series, series[1:]) if v2 < v1]
            if dips:
                warnings.append(f"historical_usd_mn is not non-decreasing ({', '.join(dips)})")
        if rate:
            for (y1, v1), (y2, v2) in zip(series, series[1:]):
                yoy = (v2 / v1) ** (1 / max(1, y2 - y1)) - 1 if v1 > 0 else 0.0
                if abs(yoy) > 2 * abs(rate):
                    warnings.append(f"historical_usd_mn {y1}->{y2} moves {yoy * 100:+.1f}%/yr, "
                                    f"more than 2x the {cagr_pct:.1f}% CAGR")
        if warnings:
            changes += [f"WARNING: {w}; history kept as raw values and not printed" for w in warnings]
        else:
            if factor != 1:
                changes.append(f"historical_usd_mn rescaled by {factor:.3f}x so the series leads smoothly into base_year_value_usd_mn")
            ms["historical_usd_mn"] = new_hist
        ms["history_check"] = {"ok": not warnings, "warnings": warnings}

    return ms, base_val, rate, base_year, forecast_year


def _reconcile_drivers_restraints(dr: dict, cagr_pct: float, changes: list[str]) -> dict:
    drivers = dr.get("drivers") or []
    restraints = dr.get("restraints") or []
    for r in restraints:
        r["cagr_drag_bps"] = abs(float(r.get("cagr_drag_bps", 0) or 0))

    restraints_total_orig = sum(r.get("cagr_drag_bps", 0) for r in restraints) or 0.0
    drivers_total_orig = sum(d.get("cagr_contribution_bps", 0) for d in drivers) or 0.0
    target_net = cagr_pct * 100.0

    # Drivers and restraints are scaled TOGETHER: keep the source's
    # restraint-to-driver ratio (capped, so restraints can't nearly cancel
    # drivers and force an inflated driver total), then solve
    # drivers - restraints = CAGR in bps.
    ratio = restraints_total_orig / drivers_total_orig if drivers_total_orig else 0.0
    ratio = min(max(ratio, 0.0), _MAX_RESTRAINT_TO_DRIVER_RATIO)
    drivers_target_total = abs(target_net) / (1 - ratio) if target_net else drivers_total_orig
    restraints_total = drivers_target_total * ratio
    if restraints and restraints_total_orig:
        rfactor = restraints_total / restraints_total_orig
        for r in restraints:
            r["cagr_drag_bps"] = round(r["cagr_drag_bps"] * rfactor, 1)
    if drivers and drivers_total_orig:
        dfactor = drivers_target_total / drivers_total_orig if drivers_total_orig else 1
        for d in drivers:
            d["cagr_contribution_bps"] = round(float(d.get("cagr_contribution_bps", 0) or 0) * dfactor, 1)
        new_net = sum(d["cagr_contribution_bps"] for d in drivers) - sum(r["cagr_drag_bps"] for r in restraints)
        if abs(new_net - target_net) > 1:
            changes.append(f"drivers/restraints bps rescaled so drivers - restraints = {target_net:.0f} bps (matches CAGR)")
    return dr


def _reconcile_porters(pf: dict, changes: list[str]) -> dict:
    forces = pf.get("forces") or {}
    scores = []
    for key, force in forces.items():
        score = round(float(force.get("score_1_5", 3) or 3), 1)
        score = min(5.0, max(1.0, score))
        label = porter_label(score)
        if force.get("label") != label or force.get("score_1_5") != score:
            changes.append(f"{key}: score set to {score}, label corrected to '{label}'")
        force["score_1_5"] = score
        force["label"] = label
        scores.append(score)
    if scores:
        overall = round(sum(scores) / len(scores), 1)
        if pf.get("overall_score_out_of_5") != overall:
            changes.append(f"overall_score_out_of_5 recomputed as the mean of the 5 force scores ({overall})")
        pf["overall_score_out_of_5"] = overall
    return pf


def _reconcile_segmentation(seg: dict, base_val: float, changes: list[str]) -> dict:
    grade = seg.get("by_grade_or_type") or []
    form = seg.get("by_form") or []
    if grade:
        total = _normalize_shares(grade, "share_pct_of_market")
        if abs(total - 100) > _SHARE_TOLERANCE:
            changes.append(f"by_grade_or_type shares normalized to 100% (were {total:.1f}%)")
        for g in grade:
            g["value_usd_mn"] = round(base_val * g["share_pct_of_market"] / 100, 2)
    if form:
        total = _normalize_shares(form, "share_pct_of_market")
        if abs(total - 100) > _SHARE_TOLERANCE:
            changes.append(f"by_form shares normalized to 100% (were {total:.1f}%)")
    return seg


def _reconcile_end_use(eu: dict, cagr_pct: float, changes: list[str]) -> dict:
    apps = eu.get("applications") or []
    if not apps:
        return eu
    total = _normalize_shares(apps, "share_pct_of_market")
    if abs(total - 100) > _SHARE_TOLERANCE:
        changes.append(f"applications shares normalized to 100% (were {total:.1f}%)")

    weighted_avg = sum(a["share_pct_of_market"] * a.get("cagr_2025_2030_pct", 0) for a in apps) / 100.0
    if weighted_avg and abs(weighted_avg - cagr_pct) > _CAGR_TOLERANCE_PP:
        scale = cagr_pct / weighted_avg
        for a in apps:
            a["cagr_2025_2030_pct"] = round(a.get("cagr_2025_2030_pct", 0) * scale, 2)
        changes.append(
            f"applications' CAGRs rescaled so their share-weighted average matches the market CAGR "
            f"({weighted_avg:.2f}% -> {cagr_pct:.2f}%)"
        )
    return eu


_STOPWORDS = {"feed", "feeds", "market", "segment", "type", "grade", "application", "applications"}


def _dimension_key(name: str) -> str:
    """Normalizes a grade/application name for cross-dimension matching --
    e.g. "Broiler Feed" and "Broiler" both reduce to "broiler", so a shared
    first/only significant word is still recognized as the same thing even
    when the two dimensions phrase it differently."""
    words = [w for w in re.findall(r"[a-z0-9]+", name.lower()) if w not in _STOPWORDS]
    return words[0] if words else name.strip().lower()


def _reconcile_cross_dimension(seg: dict, eu: dict, changes: list[str]) -> None:
    """'End-use and grade must not contradict each other when they are the
    same dimension' -- when a grade/type name and an application name refer
    to the same thing (exact match, or share their key significant word --
    e.g. "Broiler Feed" vs "Broiler"), average their shares so both report
    the same figure instead of two different ones.

    The overlap items are locked to that average and never touched again;
    the REST of each list is rescaled (independently per list, since the two
    lists can have different non-overlap items) to absorb whatever's left so
    each list still sums to 100% -- renormalizing the overlap items again
    afterward would silently pull them back apart, which is exactly the bug
    an earlier version of this function had."""
    grade_items = seg.get("by_grade_or_type") or []
    app_items = eu.get("applications") or []
    grade_by_key: dict[str, list[dict]] = {}
    for g in grade_items:
        grade_by_key.setdefault(_dimension_key(g["name"]), []).append(g)
    app_by_key: dict[str, list[dict]] = {}
    for a in app_items:
        app_by_key.setdefault(_dimension_key(a["name"]), []).append(a)

    overlap = set(grade_by_key) & set(app_by_key)
    if not overlap:
        return

    locked_total = 0.0
    for key in overlap:
        g_total = sum(g["share_pct_of_market"] for g in grade_by_key[key])
        a_total = sum(a["share_pct_of_market"] for a in app_by_key[key])
        avg = round((g_total + a_total) / 2, 2)
        if abs(g_total - avg) > 0.5 or abs(a_total - avg) > 0.5:
            names = sorted({g["name"] for g in grade_by_key[key]} | {a["name"] for a in app_by_key[key]})
            changes.append(f"{' / '.join(names)} appears in both grade/type and end-use -- shares averaged to agree ({avg}%)")
        for g in grade_by_key[key]:
            g["share_pct_of_market"] = round(avg * (g["share_pct_of_market"] / g_total if g_total else 1 / len(grade_by_key[key])), 2)
        for a in app_by_key[key]:
            a["share_pct_of_market"] = round(avg * (a["share_pct_of_market"] / a_total if a_total else 1 / len(app_by_key[key])), 2)
        locked_total += avg

    for items in (grade_items, app_items):
        others = [it for it in items if _dimension_key(it["name"]) not in overlap]
        if not others:
            # Every item overlaps (the two lists describe the same
            # dimension): nothing else can absorb the remainder, so rescale
            # this list as a whole to keep it summing to 100%.
            _normalize_shares(items, "share_pct_of_market")
            continue
        target = 100.0 - locked_total
        current = sum(it["share_pct_of_market"] for it in others) or 1.0
        factor = target / current
        for it in others:
            it["share_pct_of_market"] = round(it["share_pct_of_market"] * factor, 2)


def _cap_value_list(items: list[dict], value_key: str, name_key: str, cap: float, group: str, changes: list[str],
                    market_name: str = "") -> list[dict]:
    out = []
    for it in items:
        name = str(it.get(name_key, "") or "")
        if looks_like_leaked_search_title(name):
            changes.append(f"{group}: dropped '{name}' (looks like a leaked search-result title, not a real entry)")
            continue
        if market_name and looks_like_parent_or_regional_market(name, market_name):
            changes.append(f"{group}: dropped '{name}' (the parent market or a regional slice, not a derivative)")
            continue
        val = it.get(value_key)
        if val is not None and val > cap:
            changes.append(f"{group}: '{name}' capped from {val:,.0f} to {cap * 0.5:,.0f} (exceeded the total market size)")
            it[value_key] = round(cap * 0.5, 2)
        out.append(it)
    return out


def _reconcile_regional(reg: dict, base_val: float, changes: list[str]) -> dict:
    regions = reg.get("regions") or {}
    present = [r for r in FIXED_REGIONS if r in regions]
    if not present:
        return reg

    raw_shares = {r: float(regions[r].get("share_pct_of_global", 0) or regions[r].get("value_usd_mn", 0) or 0) for r in present}
    total_raw = sum(raw_shares.values()) or 1.0
    norm_shares = {r: raw_shares[r] / total_raw * 100 for r in present}
    if abs(total_raw - 100) > _REGION_SUM_TOLERANCE_PCT or any(
        abs(norm_shares[r] - float(regions[r].get("share_pct_of_global", 0) or 0)) > _SHARE_TOLERANCE for r in present
    ):
        changes.append(f"region shares normalized to sum to 100% (raw sum was {total_raw:.1f}%)")

    for r in present:
        rd = regions[r]
        share = round(norm_shares[r], 2)
        value = round(base_val * share / 100, 2)
        rd["share_pct_of_global"] = share
        if abs(float(rd.get("value_usd_mn", 0) or 0) - value) > 0.01 * max(value, 1):
            changes.append(f"{r}: value_usd_mn rescaled to {value:,.1f} so regions sum to the global base-year value")
        rd["value_usd_mn"] = value

        region_cagr = float(rd.get("cagr_2025_2030_pct", 0) or 0) / 100.0
        fc_value = round(value * (1 + region_cagr) ** 5, 2)  # 2025 -> 2030
        rd["forecast_value_usd_mn"] = fc_value

        countries = rd.get("countries") or []
        if countries:
            ctotal = sum(float(c.get("value_usd_mn", 0) or 0) for c in countries) or 1.0
            cfactor = value / ctotal
            if abs(cfactor - 1) > 0.02:
                changes.append(f"{r}: country values rescaled to sum to the region total")
            for c in countries:
                c["value_usd_mn"] = round(float(c.get("value_usd_mn", 0) or 0) * cfactor, 2)

        mix = rd.get("application_mix_pct") or []
        if mix:
            mtotal = _normalize_shares(mix, "share_pct")
            if abs(mtotal - 100) > _SHARE_TOLERANCE:
                changes.append(f"{r}: application_mix_pct normalized to 100% (was {mtotal:.1f}%)")

    return reg


def _reconcile_competitive(comp: dict, changes: list[str]) -> dict:
    companies = comp.get("companies") or []
    if not companies:
        return comp
    companies.sort(key=lambda c: c.get("share_pct", 0), reverse=True)
    total_named = sum(c.get("share_pct", 0) for c in companies)
    if total_named > 100:
        scale = 95.0 / total_named
        for c in companies:
            c["share_pct"] = round(c["share_pct"] * scale, 2)
        total_named = sum(c.get("share_pct", 0) for c in companies)
        changes.append(f"named-company shares rescaled to fit under 100% (were summing to {total_named / scale:.1f}%)")
    comp["companies"] = companies
    comp["others_share_pct"] = round(max(0.0, 100 - total_named), 2)
    return comp


def _reconcile_scenarios(scen: dict, base_val: float, forecast_val: float, cagr_pct: float, years: int, changes: list[str]) -> dict:
    base = scen.setdefault("base", {})
    if base.get("terminal_value_usd_mn") != forecast_val or base.get("cagr_pct") != cagr_pct:
        changes.append("scenarios.base set to exactly match Section 2's forecast value/CAGR (was a separately-jittered guess)")
    base["terminal_value_usd_mn"] = forecast_val
    base["cagr_pct"] = cagr_pct

    bear = scen.setdefault("bear", {})
    bull = scen.setdefault("bull", {})
    # The agent's bear/bull CAGRs are kept when bear < base < bull (PLAN-02: a
    # raw 18/32/45 is not rewritten). Only a value that breaks the order (or is
    # missing) is clamped, to `gap` below/above base, and that is logged.
    gap = max(_SCENARIO_MIN_GAP_PP, abs(cagr_pct) * _SCENARIO_MIN_GAP_REL)

    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    bear_raw, bull_raw = _num(bear.get("cagr_pct")), _num(bull.get("cagr_pct"))
    bear_cagr = bear_raw if bear_raw is not None and bear_raw < cagr_pct else cagr_pct - gap
    bull_cagr = bull_raw if bull_raw is not None and bull_raw > cagr_pct else cagr_pct + gap
    if bear_cagr != bear_raw:
        changes.append(f"bear CAGR {bear_raw}% is not below base {cagr_pct:.2f}%: clamped to {bear_cagr:.2f}% "
                       f"({gap:.2f} pp below base)")
    if bull_cagr != bull_raw:
        changes.append(f"bull CAGR {bull_raw}% is not above base {cagr_pct:.2f}%: clamped to {bull_cagr:.2f}% "
                       f"({gap:.2f} pp above base)")
    bear["cagr_pct"] = round(bear_cagr, 2)
    bull["cagr_pct"] = round(bull_cagr, 2)
    bear["terminal_value_usd_mn"] = round(base_val * (1 + bear_cagr / 100) ** years, 2)
    bull["terminal_value_usd_mn"] = round(base_val * (1 + bull_cagr / 100) ** years, 2)
    if not (bear["terminal_value_usd_mn"] < base["terminal_value_usd_mn"] < bull["terminal_value_usd_mn"]):
        changes.append("scenario bear/base/bull order could not be fully enforced -- flagged in validation")
    else:
        changes.append("bear/bull terminal values recomputed from their own CAGRs off the same base, enforcing bear < base < bull")
    return scen


def reconcile(fields: dict[str, Any], market_name: str,
              definition: dict | None = None) -> tuple[dict[str, Any], dict[str, list[str]]]:
    """Returns (fixed_fields, changes) -- `changes` maps group key -> list
    of human-readable descriptions of what was adjusted and why. Never
    mutates the `fields` argument -- a full deep copy is reconciled instead,
    so callers can safely compute a "before" validation report from the
    original right after calling this (a shallow copy previously left
    nested dicts aliased, silently corrupting "before" snapshots).

    `definition` (optional) is the market definition -- either the
    run_fleet.py `market_definition` dict or the client-supplied
    `definition_inclusion_exclusion` dict. When given, share-based segments
    it explicitly excludes are dropped before shares are normalized."""
    fields = copy.deepcopy(fields)
    changes: dict[str, list[str]] = {}

    terms = exclusion_terms(definition, market_name)
    if terms:
        seg = fields.get("segmentation_grade_form") or {}
        eu = fields.get("end_use_industry") or {}
        for holder, key, group in ((seg, "by_grade_or_type", "segmentation_grade_form"),
                                   (seg, "by_form", "segmentation_grade_form"),
                                   (eu, "applications", "end_use_industry")):
            if holder.get(key):
                c: list[str] = []
                holder[key] = _drop_out_of_scope(holder[key], terms, group, c)
                if c:
                    changes.setdefault(group, []).extend(c)

    base_val = forecast_val = 0.0
    cagr_pct = 0.0
    years = 10
    if "market_size" in fields and "error" not in fields["market_size"]:
        c: list[str] = []
        fields["market_size"], base_val, rate, base_year, forecast_year = _reconcile_market_size(fields["market_size"], c)
        cagr_pct = rate * 100
        years = max(1, forecast_year - base_year)
        forecast_val = fields["market_size"]["forecast_value_usd_mn"]
        if c:
            changes["market_size"] = c

    if "drivers_restraints" in fields and "error" not in fields["drivers_restraints"]:
        c = []
        fields["drivers_restraints"] = _reconcile_drivers_restraints(fields["drivers_restraints"], cagr_pct, c)
        if c:
            changes["drivers_restraints"] = c

    if "porters_five_forces" in fields and "error" not in fields["porters_five_forces"]:
        c = []
        fields["porters_five_forces"] = _reconcile_porters(fields["porters_five_forces"], c)
        if c:
            changes["porters_five_forces"] = c

    if "segmentation_grade_form" in fields and "error" not in fields["segmentation_grade_form"]:
        c = []
        fields["segmentation_grade_form"] = _reconcile_segmentation(fields["segmentation_grade_form"], base_val, c)
        if c:
            changes.setdefault("segmentation_grade_form", []).extend(c)

    if "end_use_industry" in fields and "error" not in fields["end_use_industry"]:
        c = []
        fields["end_use_industry"] = _reconcile_end_use(fields["end_use_industry"], cagr_pct, c)
        if c:
            changes.setdefault("end_use_industry", []).extend(c)

    if "segmentation_grade_form" in fields and "end_use_industry" in fields:
        c = []
        _reconcile_cross_dimension(fields["segmentation_grade_form"], fields["end_use_industry"], c)
        if c:
            # `_reconcile_cross_dimension` already keeps both lists summing
            # to 100% on its own (it redistributes the non-overlap items
            # itself) -- only `value_usd_mn` needs recomputing here to track
            # grade/type's possibly-adjusted shares.
            for g in fields["segmentation_grade_form"].get("by_grade_or_type") or []:
                g["value_usd_mn"] = round(base_val * g["share_pct_of_market"] / 100, 2)
            changes.setdefault("segmentation_grade_form", []).extend(c)

    if "sub_segments" in fields and "error" not in fields["sub_segments"] and base_val:
        c = []
        for grp in fields["sub_segments"].get("by_top_application", []) or []:
            grp["sub_segments"] = _cap_value_list(grp.get("sub_segments") or [], "value_usd_mn", "name", base_val, "sub_segments", c, market_name)
        if c:
            changes["sub_segments"] = c

    if "derivatives_pricing" in fields and "error" not in fields["derivatives_pricing"] and base_val:
        c = []
        fields["derivatives_pricing"]["derivatives"] = _cap_value_list(
            fields["derivatives_pricing"].get("derivatives") or [], "value_usd_mn", "name", base_val, "derivatives", c,
            market_name,
        )
        # A derivative that is also a grade/type in Section 5 is the same
        # entity -- it must carry the same value, not a second guess.
        grade_vals = {str(g.get("name", "")).strip().lower(): g.get("value_usd_mn")
                      for g in (fields.get("segmentation_grade_form") or {}).get("by_grade_or_type") or []}
        for d in fields["derivatives_pricing"]["derivatives"]:
            gv = grade_vals.get(str(d.get("name", "")).strip().lower())
            if gv is not None and d.get("value_usd_mn") is not None and abs(d["value_usd_mn"] - gv) > 0.01 * max(gv, 1):
                c.append(f"derivatives: '{d.get('name')}' set to {gv:,.1f} to match the same entry in grade/type "
                         f"(was {d['value_usd_mn']:,.1f})")
                d["value_usd_mn"] = gv
        tiers = sorted(fields["derivatives_pricing"].get("price_tiers") or [], key=lambda t: t.get("price_low_usd_per_unit", 0))
        prev_high = 0.0
        for t in tiers:
            lo = float(t.get("price_low_usd_per_unit", 0) or 0)
            hi = float(t.get("price_high_usd_per_unit", lo) or lo)
            if lo < prev_high:
                shift = prev_high - lo
                lo, hi = lo + shift, hi + shift
                c.append(f"price tier '{t.get('tier')}' shifted up to not overlap the previous tier")
            if hi < lo:
                lo, hi = hi, lo
            t["price_low_usd_per_unit"], t["price_high_usd_per_unit"] = round(lo, 2), round(hi, 2)
            prev_high = hi
        fields["derivatives_pricing"]["price_tiers"] = tiers
        if c:
            changes["derivatives_pricing"] = c

    if "regional" in fields and "error" not in fields["regional"] and base_val:
        c = []
        fields["regional"] = _reconcile_regional(fields["regional"], base_val, c)
        if c:
            changes["regional"] = c

    if "competitive_landscape" in fields and "error" not in fields["competitive_landscape"]:
        c = []
        fields["competitive_landscape"] = _reconcile_competitive(fields["competitive_landscape"], c)
        if c:
            changes["competitive_landscape"] = c

    if "regulation" in fields and "error" not in fields["regulation"]:
        c = []
        for a in fields["regulation"].get("compliance_areas") or []:
            lo, hi = a.get("cost_pct_of_cogs_low", 0), a.get("cost_pct_of_cogs_high", 0)
            if lo > hi:
                a["cost_pct_of_cogs_low"], a["cost_pct_of_cogs_high"] = hi, lo
                c.append(f"'{a.get('name')}': swapped low/high compliance-cost bounds (were reversed)")
        if c:
            changes["regulation"] = c

    if "investment_priorities" in fields and "error" not in fields["investment_priorities"]:
        c = []
        for o in fields["investment_priorities"].get("opportunities") or []:
            lo, hi = o.get("roi_low_pct", 0), o.get("roi_high_pct", 0)
            if lo > hi:
                o["roi_low_pct"], o["roi_high_pct"] = hi, lo
                c.append(f"'{o.get('opportunity')}': swapped low/high ROI bounds (were reversed)")
        if c:
            changes["investment_priorities"] = c

    if "scenarios" in fields and "error" not in fields["scenarios"] and base_val:
        c = []
        fields["scenarios"] = _reconcile_scenarios(fields["scenarios"], base_val, forecast_val, cagr_pct, years, c)
        if c:
            changes["scenarios"] = c

    return fields, changes


# --------------------------------------------------------------------------- #
# validate() -- read-only checks
# --------------------------------------------------------------------------- #
def validate(fields: dict[str, Any], market_name: str) -> list[str]:
    issues: list[str] = []

    ms = fields.get("market_size") or {}
    base_val = float(ms.get("base_year_value_usd_mn", 0) or 0)
    forecast_val = float(ms.get("forecast_value_usd_mn", 0) or 0)
    cagr_pct = float(ms.get("cagr_pct", 0) or 0)
    base_year = int(ms.get("base_year", 2025) or 2025)
    forecast_year = int(ms.get("forecast_year", base_year + 10) or base_year + 10)
    years = max(1, forecast_year - base_year)

    if base_val and forecast_val and cagr_pct:
        implied_cagr = ((forecast_val / base_val) ** (1 / years) - 1) * 100
        if abs(implied_cagr - cagr_pct) > _CAGR_TOLERANCE_PP:
            issues.append(f"[market_size] printed CAGR {cagr_pct:.2f}% != CAGR implied by base/forecast {implied_cagr:.2f}%")

    series = ms.get("forecast_series_usd_mn") or {}
    if series:
        years_sorted = sorted(int(y) for y in series)
        vals = [series[str(y)] for y in years_sorted]
        if any(b < a - 0.01 for a, b in zip(vals, vals[1:])):
            issues.append("[market_size] forecast_series_usd_mn is not monotonically non-decreasing")

    def _check_shares(items, key, label):
        if not items:
            return
        total = sum(float(it.get(key, 0) or 0) for it in items)
        if abs(total - 100) > _SHARE_TOLERANCE:
            issues.append(f"[{label}] shares sum to {total:.1f}%, expected 100% +/- {_SHARE_TOLERANCE}")

    seg = fields.get("segmentation_grade_form") or {}
    _check_shares(seg.get("by_grade_or_type"), "share_pct_of_market", "segmentation_grade_form.by_grade_or_type")
    _check_shares(seg.get("by_form"), "share_pct_of_market", "segmentation_grade_form.by_form")
    eu = fields.get("end_use_industry") or {}
    _check_shares(eu.get("applications"), "share_pct_of_market", "end_use_industry.applications")

    regions = (fields.get("regional") or {}).get("regions") or {}
    if regions:
        region_vals = [float(r.get("value_usd_mn", 0) or 0) for r in regions.values()]
        region_shares = [float(r.get("share_pct_of_global", 0) or 0) for r in regions.values()]
        if abs(sum(region_shares) - 100) > _SHARE_TOLERANCE:
            issues.append(f"[regional] region shares sum to {sum(region_shares):.1f}%, expected 100%")
        if base_val and abs(sum(region_vals) - base_val) > _REGION_SUM_TOLERANCE_PCT / 100 * base_val:
            issues.append(f"[regional] region values sum to {sum(region_vals):,.1f}, expected ~{base_val:,.1f} (global base-year value)")
        for rname, rdata in regions.items():
            _check_shares(rdata.get("application_mix_pct"), "share_pct", f"regional.{rname}.application_mix_pct")
            countries = rdata.get("countries") or []
            if countries:
                csum = sum(float(c.get("value_usd_mn", 0) or 0) for c in countries)
                rval = float(rdata.get("value_usd_mn", 0) or 0)
                if rval and abs(csum - rval) > 0.02 * rval:
                    issues.append(f"[regional.{rname}] countries sum to {csum:,.1f}, expected ~{rval:,.1f}")

    if base_val:
        cap = base_val * _SCOPE_BLEED_MULTIPLE
        for grp in (fields.get("sub_segments") or {}).get("by_top_application", []) or []:
            for s in grp.get("sub_segments") or []:
                v = s.get("value_usd_mn")
                if v and v > base_val:
                    issues.append(f"[sub_segments] '{s.get('name')}' = {v:,.0f} exceeds the total market ({base_val:,.0f})")
                if v and v > cap:
                    issues.append(f"[sub_segments] '{s.get('name')}' = {v:,.0f} is >3x the market size -- likely scope bleed")
        for d in (fields.get("derivatives_pricing") or {}).get("derivatives", []) or []:
            v = d.get("value_usd_mn")
            if v and v > base_val:
                issues.append(f"[derivatives_pricing] '{d.get('name')}' = {v:,.0f} exceeds the total market ({base_val:,.0f})")

    scen = fields.get("scenarios") or {}
    bear, base_s, bull = scen.get("bear", {}), scen.get("base", {}), scen.get("bull", {})
    if bear and base_s and bull:
        if not (bear.get("terminal_value_usd_mn", 0) < base_s.get("terminal_value_usd_mn", 0) < bull.get("terminal_value_usd_mn", 0)):
            issues.append("[scenarios] terminal values are not strictly bear < base < bull")
        if not (bear.get("cagr_pct", 0) < base_s.get("cagr_pct", 0) < bull.get("cagr_pct", 0)):
            issues.append("[scenarios] CAGRs are not strictly bear < base < bull")
        if abs(base_s.get("terminal_value_usd_mn", 0) - forecast_val) > 0.01 * max(forecast_val, 1):
            issues.append(f"[scenarios] base case ({base_s.get('terminal_value_usd_mn')}) != Section 2 forecast ({forecast_val})")

    pf = fields.get("porters_five_forces") or {}
    for key, force in (pf.get("forces") or {}).items():
        score = force.get("score_1_5")
        if score is not None and force.get("label") != porter_label(float(score)):
            issues.append(f"[porters_five_forces] {key}: label '{force.get('label')}' doesn't match score {score}")

    comp = fields.get("competitive_landscape") or {}
    companies = comp.get("companies") or []
    if companies and companies != sorted(companies, key=lambda c: c.get("share_pct", 0), reverse=True):
        issues.append("[competitive_landscape] companies are not sorted descending by share")
    if companies and sum(c.get("share_pct", 0) for c in companies) > 100.01:
        issues.append("[competitive_landscape] named company shares sum to more than 100%")

    return issues
