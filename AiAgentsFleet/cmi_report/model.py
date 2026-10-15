"""Builds the single numeric model every chart, table and sentence of the CMI
whitepaper reads from, so they cannot disagree.

Inputs: the fleet's consistency-reconciled whitepaper_fields.json (global
series, regions, type shares, companies, scenarios) and cmi_fields.json
(country splits, segment mixes, MEA data). Everything is forced to add up:
regions sum to global each year, countries to their region, segments to
their geography, Middle East + Africa to MEA.

Writes output/<slug>/cmi_model.json.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from AiAgentsFleet.cmi_report import geo_check, segments_check, verify_facts
from AiAgentsFleet.cmi_report.groups import DEFAULT_COUNTRIES, REST, rest_members

Y0, Y1 = 2026, 2033
YEARS = list(range(2025, Y1 + 1))
N = Y1 - Y0
MAIN_REGIONS = ["North America", "Europe", "Asia Pacific", "Latin America", "Middle East & Africa"]


# --------------------------------------------------------------------------- #
# arithmetic
# --------------------------------------------------------------------------- #
def cagr(a: float, b: float, n: int = N) -> float:
    return ((b / a) ** (1 / n) - 1) * 100 if a > 0 and b > 0 else 0.0


def allocate(total26: float, total33: float, parts: dict[str, tuple[float, float]]) -> dict[str, dict]:
    """Splits a 2026 and a 2033 total across `parts` {name: (share, cagr%)}.
    Shares are normalised to 100; the 2033 values are the shares grown at
    their own CAGRs, then scaled together so they sum to `total33` -- which
    keeps the ranking of growth rates."""
    s = sum(max(sh, 0) for sh, _ in parts.values()) or 1
    v26 = {k: total26 * max(sh, 0) / s for k, (sh, _) in parts.items()}
    raw = {k: v26[k] * (1 + g / 100) ** N for k, (_, g) in parts.items()}
    k33 = total33 / (sum(raw.values()) or 1)
    return {k: {"v26": v26[k], "v33": raw[k] * k33} for k in parts}


def series(v26: float, v33: float, v25: float | None = None) -> dict[int, float]:
    g = (v33 / v26) ** (1 / N) if v26 > 0 else 1
    out = {y: v26 * g ** (y - Y0) for y in range(Y0, Y1 + 1)}
    out[2025] = v25 if v25 is not None else v26 / g
    return dict(sorted(out.items()))


def normalise_years(rows: dict[str, dict[int, float]], totals: dict[int, float]) -> None:
    """Scales each year's rows to that year's total (stacked bars add up)."""
    for y in totals:
        s = sum(r[y] for r in rows.values()) or 1
        for r in rows.values():
            r[y] *= totals[y] / s


def node(v26: float, v33: float) -> dict:
    return {"v26": v26, "v33": v33, "incr": v33 - v26, "cagr": cagr(v26, v33)}


# --------------------------------------------------------------------------- #
# model
# --------------------------------------------------------------------------- #
def _country_parts(region: str, raw: dict, rest_cagr: float, region_cagr: float,
                   names: list[str]) -> dict[str, tuple[float, float]]:
    """{country: (share, cagr)} exactly as the agent gave them, 'Rest of' included.
    Nothing is rescaled here: allocate() normalises a split whose sum is off,
    and geo_check records that (or a blocker) in m["geo_review"]."""
    named = {c: (float((raw.get(c) or {}).get("share_pct_of_region") or 0),
                 float((raw.get(c) or {}).get("cagr_2026_2033_pct") or region_cagr)) for c in names}
    if region not in REST:                          # North America: the named countries are the region
        return named
    rest = raw.get(REST[region])
    if isinstance(rest, dict) and rest.get("share_pct_of_region") is not None:
        named[REST[region]] = (float(rest["share_pct_of_region"]),
                               float(rest.get("cagr_2026_2033_pct") or rest_cagr))
    else:                                           # answers from before PLAN-03 had no Rest-of entry
        named[REST[region]] = (max(100 - sum(s for s, _ in named.values()), 0.0), rest_cagr)
    return named


def _segments(raw: dict, names: list[str], fallback: dict[str, tuple[float, float]]) -> dict[str, tuple[float, float]]:
    """{name: (share, cagr)} for one geography, keyed by the canonical names;
    a segment the agent left out takes its global share and growth."""
    low = {k.lower(): v for k, v in (raw or {}).items()}
    out = {}
    for n in names:
        v = low.get(n.lower())
        out[n] = ((float(v.get("share_pct") or 0), float(v.get("cagr_pct") or fallback[n][1]))
                  if isinstance(v, dict) else fallback[n])
    return out


# --------------------------------------------------------------------------- #
# market definition (A1) -- every report states it, every segment obeys it
# --------------------------------------------------------------------------- #
def definition(fleet: dict, c: dict | None = None) -> dict:
    """{"definition", "measures", "value_chain_level", "included", "excluded",
    "included_items", "excluded_items", "unit", "source", "current", "check"}.

    A PLAN-04 definition (market_definition.py: measures, value-chain level,
    included/excluded lists, with its own check) is used when there is one --
    the one run_data regenerated into cmi_fields first, else the fleet's. The
    client's verbatim definition sentence stays the "definition", and every
    client exclusion stays excluded. An older definition is read as before
    (the client's verbatim text first) and marked current=False."""
    from AiAgentsFleet.market_definition import is_current, items, listed
    from AiAgentsFleet.cmi_report.guard import scope_stems
    client = fleet.get("definition_inclusion_exclusion") or {}
    gen = next((d for d in ((c or {}).get("market_definition"), fleet.get("market_definition")) if is_current(d)),
               None)
    source = "client definition" if client.get("definition") or client.get("inclusions") else "generated"
    if gen:
        inc, exc = items(gen.get("included")), items(gen.get("excluded"))
        # every client exclusion stays excluded, in the client's words when the generated list lacks it
        have = scope_stems(" ".join(exc))
        for x in re.split(r"[,;]", str(client.get("exclusions") or client.get("excluded") or "")):
            x = x.strip(" .")
            if x and not scope_stems(x) <= have:
                exc.append(x)
        return {"market_name": fleet.get("market_name") or gen.get("market_name") or "",
                "definition": client.get("definition") or gen.get("measures") or "",
                "measures": gen.get("measures") or "", "value_chain_level": gen.get("value_chain_level") or "",
                "included": listed(inc), "excluded": listed(exc), "included_items": inc, "excluded_items": exc,
                "unit": gen.get("unit") or gen.get("unit_of_measure") or "USD million, market value",
                "source": source, "current": True, "check": gen.get("check")}
    old = fleet.get("market_definition") or {}
    out = {
        "market_name": fleet.get("market_name") or "",
        "definition": client.get("definition") or "",
        "measures": "", "value_chain_level": "",
        "included": client.get("inclusions") or client.get("included") or listed(old.get("included")),
        "excluded": client.get("exclusions") or client.get("excluded") or listed(old.get("excluded")),
        "unit": old.get("unit_of_measure") or "USD million, market value",
        "source": source, "current": False, "check": None,
    }
    if not out["definition"] and out["included"]:
        out["definition"] = out["included"]
        out["included"] = ""
    return out


def clean_publisher(name: str, url: str = "") -> str:
    """A publisher's name, not a web-page title: 'Vitamin C Market Size & Share - reanin.com' -> 'Reanin'."""
    import re as _re
    n = (name or "").strip()
    if n and len(n) <= 40 and "market" not in n.lower() and " - " not in n and "." not in n:
        return n
    dom = _re.sub(r"^https?://(www\.)?", "", url or "").split("/")[0] or n.split(" - ")[-1]
    stem = dom.split(".")[0] if "." in dom else dom
    return stem[:1].upper() + stem[1:] if stem else n


def regulatory_countries(ag: dict) -> list[str]:
    """Countries that are in BOTH the regulatory scorecard and the regulator
    table (scorecard countries = detail-table countries)."""
    from AiAgentsFleet.cmi_report.groups import canonical
    reg = ag.get("regulation") or {}
    score = {canonical(k) for k in (reg.get("scorecard") or {})}
    snap = [canonical(r.get("country", "")) for r in reg.get("snapshot") or []]
    return [c for c in dict.fromkeys(snap) if c in score]


def display_name(m: dict, name: str) -> str:
    """The one name a company has everywhere in the report (its group's name in
    the share charts, e.g. 'Syngenta (incl. Valagro)')."""
    alias = m.get("vendor_alias") or {}
    n = str(name or "")
    return alias.get(n.lower()) or alias.get(n.split(" (")[0].lower()) or n


def merge_parents(shares: list[tuple[str, float]]) -> tuple[list[tuple[str, float]], list[str]]:
    """Counts a subsidiary with its parent group ('Valagro (Syngenta Group)' ->
    'Syngenta (incl. Valagro)') so no group appears twice in a share chart."""
    import re
    names = [n for n, _ in shares]

    def parent_of(name: str) -> str | None:
        m = re.search(r"\(([^)]+?)(?:\s+Group)?\)", name)
        if not m:
            return None
        p = m.group(1).strip().lower()
        return next((o for o in names if o != name and (o.lower() == p or o.lower().startswith(p + " ")
                                                       or p.startswith(o.lower()))), None)
    merged: dict[str, float] = {}
    subs: dict[str, list[str]] = {}
    notes = []
    for n, s in shares:
        p = parent_of(n)
        if p:
            merged[p] = merged.get(p, 0) + s
            subs.setdefault(p, []).append(n.split(" (")[0])
            notes.append(f"'{n}' counted within its parent '{p}'")
        else:
            merged[n] = merged.get(n, 0) + s
    out = [(f"{n} (incl. {', '.join(subs[n])})" if n in subs else n, s) for n, s in merged.items()]
    return out, notes


# --------------------------------------------------------------------------- #
# which optional sections this market gets
# --------------------------------------------------------------------------- #
_STOP = {"and", "of", "the", "for", "other", "others", "based", "a", "an", "in", "with", "&", "market"}


def _tokens(name: str, ignore: set[str]) -> set[str]:
    import re
    toks = re.split(r"[^a-z0-9]+", str(name).split(" (")[0].lower())
    return {t[:-1] if len(t) > 3 and t.endswith("s") else t for t in toks if t and t not in _STOP} - ignore


def overlap(a: list[str], b: list[str], market: str) -> float:
    """Share of the items in `a` that are essentially an item of `b`
    ('Farm Advisory & Recommendations' ~ 'Farm Advisory'); the market's own
    words are ignored because every item shares them."""
    ignore = _tokens(market, set()) - {"global"}
    if not a or not b:
        return 0.0

    def same(x, y):
        tx, ty = _tokens(x, ignore), _tokens(y, ignore)
        return bool(tx and ty) and (tx <= ty or ty <= tx or len(tx & ty) / len(tx | ty) >= 0.5)
    return sum(any(same(x, y) for y in b) for x in a) / len(a)


def sections(market: str, type_names: list[str], dem_names: list[str], c: dict) -> dict:
    """{section: {"show": bool, "why": str}} -- an optional section is shown
    only when the agent says it applies AND the data backs it up."""
    sg = c.get("segments_global") or {}
    out = {}
    real_types = [n for n in type_names if not n.lower().startswith("other")]
    real_dem = [n for n in dem_names if not n.lower().startswith("other")]
    ov = overlap(real_types, real_dem, market)
    if sg.get("type_applicable") is False:
        out["type"] = (False, f"agent: not a real segmentation ({sg.get('applicability_reason', '')})")
    elif len(real_types) < 2:
        out["type"] = (False, f"only {len(real_types)} real segment(s)")
    elif ov >= 0.5:
        out["type"] = (False, f"repeats the {(sg.get('demand_dimension') or {}).get('singular', 'demand')} split "
                              f"({ov:.0%} of segments overlap)")
    else:
        out["type"] = (True, "")
    if sg.get("demand_applicable") is False:
        out["demand"] = (False, f"agent: not a real segmentation ({sg.get('applicability_reason', '')})")
    elif len(real_dem) < 2:
        out["demand"] = (False, f"only {len(real_dem)} real segment(s)")
    else:
        out["demand"] = (True, "")
    uc = c.get("use_cases") or {}
    for key, region in (("use_cases_me", "middle_east"), ("use_cases_af", "africa")):
        n = len(uc.get(region) or [])
        out[key] = (n >= 3, "" if n >= 3 else f"only {n} use case(s) identified")
    ven = c.get("vendors") or {}
    named = [v for v in ven.get("mea_shares") or [] if "other" not in str(v.get("vendor", "")).lower()]
    out["vendor_mea"] = (len(named) >= 3, "" if len(named) >= 3 else f"only {len(named)} MEA vendor(s) identified")
    caps = ven.get("capabilities") or {}
    out["capabilities"] = (len(caps) >= 3 and len(ven.get("capability_dimensions") or []) >= 3,
                           "" if len(caps) >= 3 else "fewer than 3 vendors scored")
    out["dashboard"] = (len(ven.get("dashboard") or []) >= 2, "" if len(ven.get("dashboard") or []) >= 2
                        else "no vendor dashboard data")
    reg = c.get("regulation") or {}
    has_reg = len(reg.get("snapshot") or []) >= 2 or len(reg.get("scorecard") or {}) >= 2
    out["regulation"] = (reg.get("applicable") is not False and has_reg,
                         "" if reg.get("applicable") is not False and has_reg
                         else "no market-specific regulation identified")
    out["milestones"] = (len(reg.get("milestones") or []) >= 3, "" if len(reg.get("milestones") or []) >= 3
                         else "fewer than 3 regulatory milestones")
    return {k: {"show": bool(v[0]), "why": v[1]} for k, v in out.items()}


def global_series(fleet: dict) -> dict[int, float]:
    fs = {int(y): v for y, v in fleet["fields"]["market_size"]["forecast_series_usd_mn"].items()}
    return {y: fs[y] for y in YEARS}


def geography(fleet: dict, c: dict) -> dict:
    """Regions, the Middle East / Africa split and every country, allocated
    from the agents' answers (the re-asked regional answer when there is one),
    plus the check of those answers (m["geo_review"], geo_check.review)."""
    f = fleet["fields"]
    G = global_series(fleet)

    # ---- regions: the regional agent's answer, or its re-asked answer (geo_check.reask_regions)
    record = c.get("geo_review") or {}
    reasked = ((record.get("regional") or {}).get("answer") or {}).get("regions")
    reg_raw = reasked or f["regional"]["regions"]
    reg_parts = {r: (float(reg_raw[r]["value_usd_mn"]), float(reg_raw[r]["cagr_2025_2030_pct"])) for r in MAIN_REGIONS}
    reg = allocate(G[Y0], G[Y1], reg_parts)
    reg_series = {r: series(v["v26"], v["v33"]) for r, v in reg.items()}
    normalise_years(reg_series, G)

    # ---- which countries this market names (geography agent; template defaults otherwise)
    geo = (c.get("geography") or {}).get("countries") or DEFAULT_COUNTRIES

    keyed = geo_check.keyed      # agent output keyed by canonical country names ('United Arab Emirates' -> 'UAE')

    # ---- Middle East / Africa split of MEA
    cm = c["countries_mea"]
    mea26, mea33 = reg["Middle East & Africa"]["v26"], reg["Middle East & Africa"]["v33"]
    sub_parts = {
        "Middle East": _country_parts("Middle East", keyed(cm.get("Middle East")),
                                      cm.get("rest_of_middle_east_cagr_2026_2033_pct") or 12, 12, geo["Middle East"]),
        "Africa": _country_parts("Africa", keyed(cm.get("Africa")),
                                 cm.get("rest_of_africa_cagr_2026_2033_pct") or 12, 12, geo["Africa"]),
    }

    def weighted(parts):
        s = sum(sh for sh, _ in parts.values()) or 1
        return sum(sh * g for sh, g in parts.values()) / s

    me_share = float(cm.get("middle_east_share_of_mea_pct") or 50)
    sub = allocate(mea26, mea33, {"Middle East": (me_share, weighted(sub_parts["Middle East"])),
                                  "Africa": (100 - me_share, weighted(sub_parts["Africa"]))})

    # ---- countries
    cmain = c["countries_main"]
    countries: dict[str, dict] = {}
    for r in ["North America", "Europe", "Asia Pacific", "Latin America"]:
        parts = _country_parts(r, keyed(cmain.get(r)), reg_parts[r][1], reg_parts[r][1], geo[r])
        countries[r] = allocate(reg[r]["v26"], reg[r]["v33"], parts)
    for r in ["Middle East", "Africa"]:
        countries[r] = allocate(sub[r]["v26"], sub[r]["v33"], sub_parts[r])
    sub_series = {r: series(v["v26"], v["v33"]) for r, v in sub.items()}
    normalise_years(sub_series, reg_series["Middle East & Africa"])
    country_series = {}
    for r in ("Middle East", "Africa"):
        country_series[r] = {k: series(v["v26"], v["v33"]) for k, v in countries[r].items()}
        normalise_years(country_series[r], sub_series[r])

    review = geo_check.review(geo_check.splits_of(c), geo, record,
                              regional_raw=(fleet.get("fields_raw") or {}).get("regional") or f.get("regional"),
                              global_value=G[2025])
    return {"G": G, "reg": reg, "reg_series": reg_series, "sub": sub, "sub_series": sub_series,
            "countries": countries, "country_series": country_series, "geo": geo, "review": review}


def build(fleet: dict, cmi: dict) -> dict:
    f = fleet["fields"]
    # regulators, milestones and vendor moves as fact-checked: unconfirmed ones left out (PLAN-06)
    c = verify_facts.apply(cmi["fields"])
    gg = geography(fleet, c)
    G, reg, reg_series, sub, sub_series = gg["G"], gg["reg"], gg["reg_series"], gg["sub"], gg["sub_series"]
    countries, country_series, geo = gg["countries"], gg["country_series"], gg["geo"]

    # ---- segments: the agents' mixes, kept to the market definition (segments_check, PLAN-04):
    # excluded segments dropped, still-overlapping pairs merged, a false type split switched off
    dfn = definition(fleet, c)
    sg = c["segments_global"]
    # the type list: the agent's accepted replacement taxonomy, or the fleet's own types
    types = segments_check.types_of(f["segmentation_grade_form"]["by_grade_or_type"], sg)
    all_types = [t["name"] for t in types]
    global_problems = segments_check.check_global(sg, all_types, dfn)
    resolved = segments_check.resolve(sg, all_types, dfn)
    type_names, dem_names = resolved["names"]["type"], resolved["names"]["demand"]
    mtype, mdem = resolved["merge"]["type"], resolved["merge"]["demand"]
    tc = {k.lower(): v for k, v in (sg.get("type_cagr_2026_2033_pct") or {}).items()}
    g_cagr = cagr(G[Y0], G[Y1])
    type_global = segments_check.combine(
        {t["name"]: (t["share_pct_of_market"], float(tc.get(t["name"].lower(), g_cagr))) for t in types},
        mtype, type_names)
    dem = sg.get("demand_segments") or []
    dem_global = segments_check.combine({d["name"]: (d["share_pct_2026"], d["cagr_2026_2033_pct"]) for d in dem},
                                        mdem, dem_names)
    if resolved["type_off"]:
        c = {**c, "segments_global": {**sg, "type_applicable": False, "applicability_reason": resolved["type_off"]}}

    seg = {"Global": {"type": allocate(G[Y0], G[Y1], type_global), "demand": allocate(G[Y0], G[Y1], dem_global)}}
    smea = geo_check.keyed(c.get("segments_mea"))
    for r in ("Middle East", "Africa"):
        for unit, v in countries[r].items():
            raw = smea.get(unit) or {}
            seg[unit] = {
                "type": allocate(v["v26"], v["v33"], _segments(segments_check.merge_raw(raw.get("type"), mtype),
                                                               type_names, type_global)),
                "demand": allocate(v["v26"], v["v33"], _segments(segments_check.merge_raw(raw.get("demand"), mdem),
                                                                 dem_names, dem_global)),
            }
        # the sub-region's segments are the sum of its countries' -- one set of numbers
        seg[r] = {dim: {n: {"v26": sum(seg[u][dim][n]["v26"] for u in countries[r]),
                            "v33": sum(seg[u][dim][n]["v33"] for u in countries[r])} for n in names}
                  for dim, names in (("type", type_names), ("demand", dem_names))}

    # the MEA aggregate of the country mixes against the global mix (x2 either way): re-asked once
    # in run_data; still beyond it is a warning -- never blended toward the global mix
    mea26 = (sub["Middle East"]["v26"] + sub["Africa"]["v26"]) or 1
    mix_problems = []
    for dim, names in (("type", type_names), ("demand", dem_names)):
        gl = {n: seg["Global"][dim][n]["v26"] * 100 / G[Y0] for n in names}
        me = {n: (seg["Middle East"][dim][n]["v26"] + seg["Africa"][dim][n]["v26"]) * 100 / mea26 for n in names}
        mix_problems += segments_check.check_mix(gl, me, dim, (c.get("segments_mea") or {}).get("mea_reason"))
    # every country's mix a copy (+/-10%) of the global one is not an estimate either
    dims = (("type", type_names), ("demand", dem_names))
    copied = segments_check.copies_global(
        {u: {dim: {n: seg[u][dim][n]["v26"] * 100 / (sum(x["v26"] for x in seg[u][dim].values()) or 1)
                   for n in names} for dim, names in dims}
         for r in ("Middle East", "Africa") for u in countries[r]},
        {dim: {n: seg["Global"][dim][n]["v26"] * 100 / G[Y0] for n in names} for dim, names in dims})
    segment_review = segments_check.review(
        dfn, c.get("segment_review"), global_problems=global_problems, resolved=resolved,
        mix_problems=mix_problems, uc_problems=segments_check.check_use_cases(c.get("use_cases"), dfn),
        copied=copied, mea_names_mismatch=segments_check.mea_names_differ(
            c.get("segments_mea"), [n for n in type_names if not n.lower().startswith("other")], mtype))
    seg_defs = {dim: {n: d for n in names if (d := segments_check.defs(sg, dim).get(n.lower()))}
                for dim, names in (("type", type_names), ("demand", dem_names))}

    # ---- scenarios (2026 base, each case compounding at its own CAGR)
    sc = f.get("scenarios") or {}
    base_c = g_cagr
    cons_c = float((sc.get("bear") or {}).get("cagr_pct") or base_c - 3)
    opt_c = float((sc.get("bull") or {}).get("cagr_pct") or base_c + 3)
    if not cons_c < base_c - 0.5:
        cons_c = base_c - 3
    if not opt_c > base_c + 0.5:
        opt_c = base_c + 3
    scen = {}
    for name, cg in (("Conservative", cons_c), ("Base", base_c), ("Optimistic", opt_c)):
        scen[name] = {"cagr": cg, "series": {y: G[Y0] * (1 + cg / 100) ** (y - Y0) for y in range(Y0, Y1 + 1)}}
    scen["Base"]["series"] = {y: G[y] for y in range(Y0, Y1 + 1)}

    # ---- vendors
    comp = f.get("competitive_landscape") or {}
    gshares = [(x["name"], x["share_pct"]) for x in comp.get("companies") or []]
    others = max(0.0, 100 - sum(s for _, s in gshares))
    merge_notes: list[str] = []
    # like-for-like: a company that does not earn the revenue the definition measures
    # (e.g. a feed supplier in a farming market) is not charted against those that do
    cls = {str(x.get("company", "")).lower(): x for x in (c.get("vendors") or {}).get("company_classification") or []}
    kept = []
    for n, s in gshares:
        k = cls.get(n.lower())
        if k and k.get("earns_defined_revenue") is False:
            others += s
            merge_notes.append(f"'{n}' ({k.get('business_type', 'other business')}) left out of the share chart: "
                               f"it does not earn the revenue this market measures")
        else:
            kept.append((n, s))
    # same parent group (per the classification) -> one entry
    by_parent: dict[str, list[tuple[str, float]]] = {}
    for n, s in kept:
        p = str((cls.get(n.lower()) or {}).get("parent_group") or n).strip()
        by_parent.setdefault(p.lower(), []).append((n, s))
    gshares = []
    for members in by_parent.values():
        if len(members) == 1:
            gshares.append(members[0])
        else:
            lead = max(members, key=lambda kv: kv[1])[0]
            subs = [n.split(" (")[0] for n, _ in members if n != lead]
            gshares.append((f"{lead.split(' (')[0]} (incl. {', '.join(subs)})", sum(s for _, s in members)))
            merge_notes.append(f"{', '.join(n for n, _ in members)} counted as one group")
    gshares, more = merge_parents(gshares)
    merge_notes += more
    # one name per entity: a company merged into a group in the global chart keeps that
    # group's name in the MEA chart too
    alias = {}
    for disp, _ in gshares:
        base = disp.split(" (incl. ")[0]
        members = [base] + (disp.split(" (incl. ")[1].rstrip(")").split(", ") if " (incl. " in disp else [])
        for mbr in members:
            alias[mbr.lower()] = disp
            alias[mbr.split(" (")[0].lower()] = disp
    for k in cls.values():
        p = str(k.get("parent_group") or "").lower()
        if p and str(k.get("company", "")).lower() in alias:
            alias.setdefault(p, alias[str(k["company"]).lower()])
    mea_sh = [(x["vendor"], float(x["share_pct"])) for x in (c.get("vendors") or {}).get("mea_shares") or []]
    tot = sum(s for _, s in mea_sh) or 1
    mea_named: dict[str, float] = {}
    for n, s in mea_sh:
        key = n.lower()
        disp = alias.get(key) or alias.get(key.split(" (")[0]) or n
        if disp != n:
            merge_notes.append(f"MEA chart: '{n}' shown as '{disp}' (same entity as the global chart)")
        mea_named[disp] = mea_named.get(disp, 0) + s * 100 / tot
    mea_sh, merge_mea = merge_parents(list(mea_named.items()))
    merge_notes += merge_mea

    # published third-party figures: only those measuring the same scope as this report
    # (a figure far outside the CMI base describes a parent or adjacent market). An
    # unsourced headline is a CMI estimate with no published comparison at all (PLAN-02).
    check = size_check(fleet)
    published, pub_notes = [], []
    pub_raw = (c.get("published") or {}).get("global_2025") or [] if check["status"] == "sourced" else []
    for p in pub_raw:
        p = {**p, "publisher": clean_publisher(p.get("publisher", ""), p.get("url", ""))}
        ratio = float(p.get("value_usd_bn") or 0) * 1000 / G[2025] if G[2025] else 0
        if 1 / 3 <= ratio <= 3:
            published.append(p)
        else:
            pub_notes.append(f"Published figure from {p.get('publisher')} left out: it is "
                             f"{'more than three times' if ratio > 3 else 'less than a third of'} the CMI base, "
                             f"i.e. it measures a different market scope")
    merge_notes += pub_notes

    # value-chain share ranges: midpoints must sum to ~100% (share of a whole)
    vchain = [dict(s) for s in (c.get("vendors") or {}).get("value_chain") or []]
    mids = sum((float(s.get("share_low_pct") or 0) + float(s.get("share_high_pct") or 0)) / 2 for s in vchain)
    if vchain and mids and abs(mids - 100) > 2:
        k = 100 / mids
        for s in vchain:
            s["share_low_pct"] = round(float(s.get("share_low_pct") or 0) * k)
            s["share_high_pct"] = round(float(s.get("share_high_pct") or 0) * k)
        merge_notes.append(f"Value-chain share ranges rescaled so their midpoints sum to 100% (they summed to "
                           f"{mids:.0f}%)")

    def fold(shares, keep=7):
        """The slices a donut shows: the `keep` largest named vendors plus one
        'Regional & other' slice holding everyone else."""
        named = sorted(((n, s) for n, s in shares if n.lower() not in ("others", "regional & other")),
                       key=lambda kv: -kv[1])
        rest = sum(s for n, s in shares if n.lower() in ("others", "regional & other")) + sum(s for _, s in named[keep:])
        return named[:keep] + [("Regional & other", rest)]

    def nodes(d):
        return {k: node(v["v26"], v["v33"]) for k, v in d.items()}

    return {
        "market": fleet["market_name"],
        "years": YEARS,
        "global": {**node(G[Y0], G[Y1]), "v25": G[2025], "series": G},
        "regions": {r: {**node(v["v26"], v["v33"]), "v25": reg_series[r][2025], "series": reg_series[r]}
                    for r, v in reg.items()},
        "subregions": {r: {**node(v["v26"], v["v33"]), "v25": sub_series[r][2025], "series": sub_series[r]}
                       for r, v in sub.items()},
        "countries": {r: nodes(v) for r, v in countries.items()},
        "country_series": country_series,
        "segments": {geo: {dim: nodes(d) for dim, d in dims.items()} for geo, dims in seg.items()},
        "dimensions": {
            "type": sg.get("type_dimension") or {"singular": "Type", "plural": "Types"},
            "demand": sg.get("demand_dimension") or {"singular": "End User", "plural": "End Users"},
        },
        # full profiles (with segment charts) in Chapters 4/5: EVERY named country, largest first,
        # then the remainder -- every country in those tables gets one
        "profiled": {r: sorted((k for k in countries[r] if not k.startswith("Rest of")),
                               key=lambda k: -countries[r][k]["v26"]) + [REST[r]]
                     for r in ("Middle East", "Africa")},
        "geography": geo,
        "geography_why": (c.get("geography") or {}).get("why") or {},
        # what each 'Rest of' row covers: MEMBERS minus the named countries (data, not LLM text)
        "rest_members": rest_members(geo),
        "geo_review": gg["review"],
        "scenarios": scen,
        "vendor_share_global": gshares + ([("Others", others)] if others > 0.5 else []),
        "vendor_share_mea": mea_sh,
        "vendor_chart_global": fold(gshares + ([("Others", others)] if others > 0.5 else [])),
        "vendor_chart_mea": fold(mea_sh),
        "sections": sections(fleet["market_name"], type_names, dem_names, c),
        "definition": dfn,
        "segment_review": segment_review,
        "published_2025": published,
        "size_check": check,
        "fact_review": verify_facts.review(cmi["fields"]),
        "value_chain": vchain,
        "vendor_alias": alias,
        "segment_definitions": seg_defs,
        "allocation_rules": [r for r in sg.get("allocation_rules") or [] if isinstance(r, str) and r.strip()],
        "fleet_drivers": {k: v for k, v in (f.get("drivers_restraints") or {}).items() if k != "_sources"},
        "corrections": merge_notes,
    }


def size_check(fleet: dict) -> dict:
    """The market_size agent's headline check (cmi_report/verify_size.py), as
    recorded by the fleet run. A run without one is treated as unsourced."""
    for part in ("fields_raw", "fields"):
        chk = (((fleet.get(part) or {}).get("market_size") or {}).get("_sources") or {}).get("size_check")
        if chk:
            return {k: v for k, v in chk.items()}
    return {"status": "unsourced", "notes": ["no size check recorded (fleet run predates PLAN-02)"],
            "source": None, "cagr_sourced": False}


def write(market_dir: Path) -> Path:
    fleet = json.loads((market_dir / "whitepaper_fields.json").read_text(encoding="utf-8"))
    cmi = json.loads((market_dir / "cmi_fields.json").read_text(encoding="utf-8"))
    m = build(fleet, cmi)
    out = market_dir / "cmi_model.json"
    out.write_text(json.dumps(m, indent=1, ensure_ascii=False), encoding="utf-8")
    # the check the report was built on, beside it (same content as m["size_check"])
    (market_dir / "market_size_check.json").write_text(json.dumps(m["size_check"], indent=2, ensure_ascii=False),
                                                        encoding="utf-8")
    return out
