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
from pathlib import Path

from AiAgentsFleet.cmi_report.groups import DEFAULT_COUNTRIES, REST

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
    named = {c: (float((raw.get(c) or {}).get("share_pct_of_region") or 0),
                 float((raw.get(c) or {}).get("cagr_2026_2033_pct") or region_cagr)) for c in names}
    total = sum(s for s, _ in named.values())
    if region not in REST:                          # North America: the named countries are the region
        return named
    if total > 92:                                  # leave a realistic remainder
        named = {c: (s * 88 / total, g) for c, (s, g) in named.items()}
        total = 88
    named[REST[region]] = (100 - total, rest_cagr)
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
def definition(fleet: dict) -> dict:
    """{"definition", "included", "excluded", "unit"} from either stored
    format: the client's verbatim definition/inclusions/exclusions first,
    else the generated included/excluded."""
    client = fleet.get("definition_inclusion_exclusion") or {}
    gen = fleet.get("market_definition") or {}
    out = {
        "definition": client.get("definition") or "",
        "included": client.get("inclusions") or client.get("included") or gen.get("included") or "",
        "excluded": client.get("exclusions") or client.get("excluded") or gen.get("excluded") or "",
        "unit": gen.get("unit_of_measure") or "USD million, market value",
        "source": "client definition" if client.get("definition") or client.get("inclusions") else "generated",
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


def build(fleet: dict, cmi: dict) -> dict:
    f, c = fleet["fields"], cmi["fields"]
    ms = f["market_size"]
    fs = {int(y): v for y, v in ms["forecast_series_usd_mn"].items()}
    G = {y: fs[y] for y in YEARS}

    # ---- regions
    reg_raw = f["regional"]["regions"]
    reg_parts = {r: (reg_raw[r]["value_usd_mn"], reg_raw[r]["cagr_2025_2030_pct"]) for r in MAIN_REGIONS}
    reg = allocate(G[Y0], G[Y1], reg_parts)
    reg_series = {r: series(v["v26"], v["v33"]) for r, v in reg.items()}
    normalise_years(reg_series, G)

    # ---- which countries this market names (geography agent; template defaults otherwise)
    geo = (c.get("geography") or {}).get("countries") or DEFAULT_COUNTRIES

    def keyed(raw: dict) -> dict:
        """Agent output keyed by canonical country names ('United Arab Emirates' -> 'UAE')."""
        from AiAgentsFleet.cmi_report.groups import canonical
        return {canonical(k): v for k, v in (raw or {}).items()}

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

    # ---- segments
    sg = c["segments_global"]
    types = f["segmentation_grade_form"]["by_grade_or_type"]
    type_names = [t["name"] for t in types]
    tc = {k.lower(): v for k, v in (sg.get("type_cagr_2026_2033_pct") or {}).items()}
    g_cagr = cagr(G[Y0], G[Y1])
    type_global = {t["name"]: (t["share_pct_of_market"], float(tc.get(t["name"].lower(), g_cagr))) for t in types}
    dem = sg.get("demand_segments") or []
    dem_names = [d["name"] for d in dem]
    dem_global = {d["name"]: (d["share_pct_2026"], d["cagr_2026_2033_pct"]) for d in dem}

    seg = {"Global": {"type": allocate(G[Y0], G[Y1], type_global), "demand": allocate(G[Y0], G[Y1], dem_global)}}
    smea = keyed(c.get("segments_mea"))
    for r in ("Middle East", "Africa"):
        for unit, v in countries[r].items():
            raw = smea.get(unit) or {}
            seg[unit] = {
                "type": allocate(v["v26"], v["v33"], _segments(raw.get("type"), type_names, type_global)),
                "demand": allocate(v["v26"], v["v33"], _segments(raw.get("demand"), dem_names, dem_global)),
            }
        # the sub-region's segments are the sum of its countries' -- one set of numbers
        seg[r] = {dim: {n: {"v26": sum(seg[u][dim][n]["v26"] for u in countries[r]),
                            "v33": sum(seg[u][dim][n]["v33"] for u in countries[r])} for n in names}
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
    # (a figure far outside the CMI base describes a parent or adjacent market)
    published, pub_notes = [], []
    for p in (c.get("published") or {}).get("global_2025") or []:
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
        "scenarios": scen,
        "vendor_share_global": gshares + ([("Others", others)] if others > 0.5 else []),
        "vendor_share_mea": mea_sh,
        "vendor_chart_global": fold(gshares + ([("Others", others)] if others > 0.5 else [])),
        "vendor_chart_mea": fold(mea_sh),
        "sections": sections(fleet["market_name"], type_names, dem_names, c),
        "definition": definition(fleet),
        "published_2025": published,
        "value_chain": vchain,
        "vendor_alias": alias,
        "segment_definitions": sg.get("segment_definitions") or {},
        "allocation_rules": [r for r in sg.get("allocation_rules") or [] if isinstance(r, str) and r.strip()],
        "fleet_drivers": {k: v for k, v in (f.get("drivers_restraints") or {}).items() if k != "_sources"},
        "corrections": merge_notes,
    }


def write(market_dir: Path) -> Path:
    fleet = json.loads((market_dir / "whitepaper_fields.json").read_text(encoding="utf-8"))
    cmi = json.loads((market_dir / "cmi_fields.json").read_text(encoding="utf-8"))
    m = build(fleet, cmi)
    out = market_dir / "cmi_model.json"
    out.write_text(json.dumps(m, indent=1, ensure_ascii=False), encoding="utf-8")
    return out
