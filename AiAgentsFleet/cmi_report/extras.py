"""Report data the CMI agents (run_data.py) do not produce, asked once per market and cached in
output/<slug>/report_extras.json (changes.txt, 2026-10-19):

- the segment mix of each non-MEA region, for the comparative charts by region in 3.2 (the MEA mix
  is the sum of its country mixes; every region's split is reconciled to the global segment totals);
- the global vendor landscape by tier (manufacturers only) for the pyramid in chapter 7;
- the global impact matrix (2.3.1), from the fleet's global drivers and restraints;
- the global recommendations by stakeholder (8.1.1).

The regional shares only seed the split: region totals and global segment totals are the model's,
so the chart never shows a number that disagrees with the rest of the report.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from AiAgentsFleet.cmi_report.model import Y0, Y1, node

VERSION = 1
OTHER_REGIONS = ("North America", "Latin America", "Europe", "Asia Pacific")
MEA = "Middle East & Africa"

_SYSTEM = ("You are a senior market research analyst at Coherent Market Insights. Answer with your best "
           "industry knowledge, consistent with the figures given. Never invent precise sources; give "
           "estimates only where asked.")


def factor_name(name: str) -> str:
    """A fleet factor name without a figure in brackets ('... efficiency (5-20% field trial gains)'):
    the figure is not in the number register, and the chart beside it carries the impact."""
    name = re.sub(r"\s*\([^)]*\d[^)]*\)", "", str(name))
    # and a clause carrying a figure ('..., especially feed accounting for ~69% of expenses')
    name = re.sub(r"[,;]\s*[^,;()]*\d[^,;()]*", "", name)
    return name.strip().rstrip(",;:")


def _ask_mix(market: str, m: dict, llm) -> dict:
    dims = {d: list(m["segments"]["Global"][d]) for d in ("type", "demand")}
    gl = {d: {n: round(v["v26"] * 100 / m["global"]["v26"], 1) for n, v in m["segments"]["Global"][d].items()}
          for d in dims}
    user = (f"Market: {market}\nGlobal {Y0} segment mix (% of global value):\n"
            f"  by {m['dimensions']['type']['singular']}: {json.dumps(gl['type'])}\n"
            f"  by {m['dimensions']['demand']['singular']}: {json.dumps(gl['demand'])}\n\n"
            f"For each region in {list(OTHER_REGIONS)}, estimate its {Y0} mix (% of that region's value, "
            f"summing to 100) for both dimensions, using exactly these segment names. Reflect real regional "
            f"differences (crops grown, industry structure, regulation).\n"
            'Return {"regions": {<region>: {"type": {<segment>: <pct>}, "demand": {<segment>: <pct>}}}}')
    return llm.complete_json(_SYSTEM, user)


def _ras(seed: dict[str, dict[str, float]], row_tot: dict[str, float], col_tot: dict[str, float],
         rounds: int = 200) -> dict[str, dict[str, float]]:
    """Iterative proportional fitting: the seed matrix scaled until each region sums to its
    total and each segment to its total (both from the model)."""
    x = {r: {c: max(0.01, float(v)) for c, v in row.items()} for r, row in seed.items()}
    for _ in range(rounds):
        for r in x:
            s = sum(x[r].values()) or 1
            x[r] = {c: v * row_tot[r] / s for c, v in x[r].items()}
        for c in col_tot:
            s = sum(x[r][c] for r in x) or 1
            for r in x:
                x[r][c] *= col_tot[c] / s
    return x


def regional_mix(m: dict, raw: dict) -> dict[str, dict[str, dict[str, float]]]:
    """{dim: {region: {segment: value Y0}}} for the five regions; the four non-MEA regions reconciled
    (RAS) to the region totals and to global-minus-MEA segment totals. Empty when the answer is unusable."""
    out = {}
    regs = (raw or {}).get("regions") or {}
    for dim in ("type", "demand"):
        names = list(m["segments"]["Global"][dim])
        mea = {n: m["segments"]["Middle East"][dim][n]["v26"] + m["segments"]["Africa"][dim][n]["v26"]
               for n in names}
        seed = {}
        for r in OTHER_REGIONS:
            got = {str(k).lower(): v for k, v in ((regs.get(r) or {}).get(dim) or {}).items()}
            if not all(isinstance(got.get(n.lower()), (int, float)) for n in names):
                break
            seed[r] = {n: float(got[n.lower()]) for n in names}
        if len(seed) < len(OTHER_REGIONS):
            continue
        col = {n: max(0.0, m["segments"]["Global"][dim][n]["v26"] - mea[n]) for n in names}
        rows = _ras(seed, {r: m["regions"][r]["v26"] for r in OTHER_REGIONS}, col)
        order = sorted(OTHER_REGIONS, key=lambda r: -m["regions"][r]["v26"])
        out[dim] = {**{r: rows[r] for r in order}, MEA: mea}
    return out


def _ask_country_mix(market: str, m: dict, llm) -> dict:
    """Each non-MEA region's countries: their Y0 segment mix (for the regional chapters' mix charts)."""
    dims = {d: list(m["segments"]["Global"][d]) for d in ("type", "demand")}
    out = {}
    for r in OTHER_REGIONS:
        user = (f"Market: {market}\nRegion: {r}; countries: {list(m['countries'][r])}\n"
                f"Segments by {m['dimensions']['type']['singular']}: {dims['type']}\n"
                f"Segments by {m['dimensions']['demand']['singular']}: {dims['demand']}\n\n"
                f"For each country above, estimate its {Y0} mix (% of that country's value, summing to 100) for "
                f"both dimensions, using exactly these segment and country names. Reflect real differences "
                f"(crops grown, industry structure, regulation).\n"
                'Return {"countries": {<country>: {"type": {<segment>: <pct>}, "demand": {<segment>: <pct>}}}}')
        out[r] = llm.complete_json(_SYSTEM, user)
    return out


def region_segments(m: dict, mix: dict) -> dict[str, dict[str, dict[str, dict]]]:
    """{region: {dim: {segment: {v26, v33, incr, cagr}}}} for the non-MEA regions. Y0 from the regional
    mix; the forecast year reconciled the same way (seed = Y0 value grown at the segment's global CAGR;
    region totals and global-minus-MEA segment totals from the model)."""
    out: dict = {r: {} for r in OTHER_REGIONS}
    for dim, rows26 in mix.items():
        names = list(m["segments"]["Global"][dim])
        g = m["segments"]["Global"][dim]
        mea33 = {n: m["segments"]["Middle East"][dim][n]["v33"] + m["segments"]["Africa"][dim][n]["v33"] for n in names}
        seed = {r: {n: rows26[r][n] * (g[n]["v33"] / g[n]["v26"] if g[n]["v26"] else 1) for n in names}
                for r in OTHER_REGIONS}
        rows33 = _ras(seed, {r: m["regions"][r]["v33"] for r in OTHER_REGIONS},
                      {n: max(0.0, g[n]["v33"] - mea33[n]) for n in names})
        for r in OTHER_REGIONS:
            out[r][dim] = {n: node(rows26[r][n], rows33[r][n]) for n in names}
    return out


def country_mix(m: dict, raw: dict, segs: dict) -> dict[str, dict[str, dict[str, dict[str, float]]]]:
    """{region: {dim: {country: {segment: value Y0}}}}: the countries' mixes reconciled (RAS) to the country
    totals and to the region's segment totals. A region whose answer is unusable is left out."""
    out: dict = {}
    for r in OTHER_REGIONS:
        got = {str(k).lower(): v for k, v in (((raw or {}).get(r) or {}).get("countries") or {}).items()}
        for dim in segs.get(r) or {}:
            names = list(segs[r][dim])
            seed = {}
            for c in m["countries"][r]:
                row = {str(k).lower(): v for k, v in ((got.get(c.lower()) or {}).get(dim) or {}).items()}
                if not all(isinstance(row.get(n.lower()), (int, float)) for n in names):
                    break
                seed[c] = {n: float(row[n.lower()]) for n in names}
            if len(seed) < len(m["countries"][r]):
                continue
            fit = _ras(seed, {c: m["countries"][r][c]["v26"] for c in seed},
                       {n: segs[r][dim][n]["v26"] for n in names})
            out.setdefault(r, {})[dim] = {c: fit[c] for c in sorted(fit, key=lambda c: -m["countries"][r][c]["v26"])}
    return out


def _ask_tiers(market: str, fleet: dict, llm) -> dict:
    comp = (fleet["fields"].get("competitive_landscape") or {}).get("companies") or []
    known = [f"{c['name']} ({c['share_pct']}%)" if c.get("share_pct") else c["name"] for c in comp]
    user = (f"Market: {market}\nCompanies and shares from CMI desk research: {', '.join(known) or 'none'}\n\n"
            f"Group the leading MANUFACTURERS of this product worldwide (no distributors, retailers, "
            f"investors or research bodies; one name per corporate group, e.g. a subsidiary under its parent) "
            f"into Tier 1 (global leaders), Tier 2 (established multinationals / strong specialists) and "
            f"Tier 3 (regional specialists). List only the top 3-5 players per tier. Give each tier's COMBINED "
            f"share of global market value in {Y0 - 1} and the share of all other manufacturers (Others); the "
            f"four shares sum to 100 and stay consistent with the shares above.\n"
            'Return {"tiers": [{"name": "Tier 1", "share_pct": <n>, "players": [..]}, {"name": "Tier 2", ...}, '
            '{"name": "Tier 3", ...}], "others_share_pct": <n>}')
    return llm.complete_json(_SYSTEM, user)


def _ask_tier_names(raw: dict, llm) -> dict:
    """Current names of the tier players: a company that rebranded or was acquired is shown under its
    current name or its acquirer (the plausibility review flagged 'Italpollina', now Hello Nature)."""
    names = [p for t in (raw or {}).get("tiers") or [] for p in t.get("players") or []]
    if not names:
        return {}
    # web evidence per company: model knowledge alone missed 'Italpollina -> Hello Nature'
    from me_engine.generation.web_evidence import search_snippets_with_sources
    ev = []
    for n in names:
        bare = re.sub(r"\s*\(.*\)", "", n)
        for h in search_snippets_with_sources(f"{bare} rebranded OR renamed OR acquired by", max_results=3):
            ev.append(f"[{bare}] {h['text'][:300]}")
    user = (f"Companies: {json.dumps(names, ensure_ascii=False)}\n\nEVIDENCE (web search):\n" + "\n".join(ev) + "\n\n"
            f"For each company give its name as of {Y0}: if it rebranded, the new brand; if it was acquired and no "
            f"longer trades as an independent manufacturer, the acquiring group. Use the EVIDENCE; only changes it "
            f"or well-established knowledge supports; otherwise repeat the name unchanged.\n"
            'Return {"companies": [{"name": <exactly as given>, "current": "<current name>", '
            '"change": "none" | "rebranded" | "acquired"}]}')
    return llm.complete_json(_SYSTEM, user)


def current_names(tiers: list[dict], check: dict) -> list[dict]:
    """Tier players under their current names: 'Hello Nature (formerly Italpollina)'; an acquired company
    becomes its acquirer, and is dropped when the acquirer is already listed."""
    got = {str(c.get("name", "")).lower(): c for c in (check or {}).get("companies") or [] if isinstance(c, dict)}
    listed = {p.split(" (")[0].lower() for t in tiers for p in t.get("players") or []}
    seen: set[str] = set()
    for t in tiers:
        out = []
        for p in t.get("players") or []:
            c = got.get(p.lower()) or {}
            cur, change = str(c.get("current") or p).strip(), c.get("change")
            if cur.isupper() and len(cur) > 4:                    # 'HELLO NATURE' -> 'Hello Nature'
                cur = cur.title()
            bare = re.sub(r"\s*\(.*\)", "", p).strip()
            inner = (re.search(r"\(([^)]+)\)", p) or [None, ""])[1]
            if change == "rebranded" and cur.lower() != p.lower():
                # 'Novonesis (Novozymes)' already names both: the old name is the bracket, not the bare name
                old = inner if bare.lower() == cur.lower() and inner else bare
                p = f"{cur} (formerly {old})"
            elif change == "acquired" and cur.lower() not in (p.lower(), bare.lower()):
                if cur.split()[0].lower() in {x.split()[0] for x in listed}:
                    continue                                         # its parent is already listed
                p = f"{bare} (part of {cur})"                        # the brand stays: the acquirer may not make it
            key = p.split(" (")[0].lower()
            if key not in seen:
                seen.add(key)
                out.append(p)
        t["players"] = out
    return tiers


def vendor_tiers(raw: dict, check: dict | None = None) -> list[dict]:
    """[{name, share_pct, players}] for Tier 1-3 and Others, shares scaled to sum to 100 (one decimal)."""
    tiers = [t for t in (raw or {}).get("tiers") or [] if t.get("players") and t.get("share_pct") is not None][:3]
    if len(tiers) < 3:
        return []
    rows = [{"name": f"Tier {k}", "share_pct": float(t["share_pct"]),
             "players": [str(p).strip() for p in t["players"] if str(p).strip()][:5]}
            for k, t in enumerate(tiers, start=1)]
    # one name per corporate group: 'Valagro S.p.A. (Syngenta)' goes when Syngenta is already listed
    heads = {p.split()[0].lower() for r in rows for p in r["players"]}
    for r in rows:
        r["players"] = [p for p in r["players"]
                        if not ((mm := re.search(r"\(([^)]+)\)", p)) and mm.group(1).split()[0].lower() in heads
                                and mm.group(1).split()[0].lower() != p.split()[0].lower())]
    rows = current_names(rows, check or {})
    if sum(1 for r in rows if r["players"]) < 3:
        return []
    rows.append({"name": "Others", "share_pct": float((raw or {}).get("others_share_pct") or 0), "players": [],
                 "note": "Smaller regional and local manufacturers"})
    tot = sum(r["share_pct"] for r in rows) or 1
    for r in rows:
        r["share_pct"] = round(r["share_pct"] * 100 / tot, 1)
    rows[-1]["share_pct"] = round(100 - sum(r["share_pct"] for r in rows[:-1]), 1)
    return rows


def _ask_matrix(market: str, m: dict, llm) -> dict:
    fd = m.get("fleet_drivers") or {}
    facs = [x["name"] for x in (fd.get("drivers") or [])[:4] + (fd.get("restraints") or [])[:3]]
    user = (f"Market: {market} (global view)\nFactors: {json.dumps(facs)}\n\n"
            f"For each factor give its timeframe of impact (one of Short, Short-Mid, Mid, Mid-Long, Short-Long) "
            f"and the two or three world regions most exposed (from North America, Latin America, Europe, "
            f"Asia Pacific, Middle East & Africa).\n"
            'Return {"rows": [{"factor": <exactly as given>, "timeframe": "...", "most_exposed": "A, B"}]}')
    return llm.complete_json(_SYSTEM, user)


def global_matrix(m: dict, raw: dict) -> list[list[str]]:
    """factor | type | impact | timeframe | most exposed regions: the fleet's global factors, the impact
    label from their bps relative to the largest (>= 60% High, >= 30% Medium, else Low)."""
    fd = m.get("fleet_drivers") or {}
    items = [(x["name"], "Driver", abs(float(x.get("cagr_contribution_bps") or 0))) for x in (fd.get("drivers") or [])[:4]]
    items += [(x["name"], "Restraint", abs(float(x.get("cagr_drag_bps") or 0))) for x in (fd.get("restraints") or [])[:3]]
    top = max((v for *_, v in items), default=0) or 1
    got = {str(r.get("factor", "")).lower(): r for r in (raw or {}).get("rows") or []}
    rows = []
    for name, kind, v in items:
        r = got.get(name.lower()) or {}
        rows.append([factor_name(name), kind, "High" if v / top >= 0.6 else "Medium" if v / top >= 0.3 else "Low",
                     str(r.get("timeframe") or ""), str(r.get("most_exposed") or "")])
    return rows


def _ask_stakeholders(market: str, llm) -> dict:
    user = (f"Market: {market} (global view, {Y0}-{Y1})\n\n"
            f"Write strategic recommendations for 4-5 stakeholder groups of the GLOBAL market (manufacturers, "
            f"distributors, growers or end users, investors, regulators or research bodies -- whichever fit "
            f"this market). No numbers, no company names, no Middle East or Africa specifics.\n"
            'Return {"stakeholders": [{"stakeholder": "...", "priority_moves": "2-3 short sentences", '
            '"kpis": "3 KPIs separated by semicolons"}]}')
    return llm.complete_json(_SYSTEM, user)


def _ask_phases(market: str, llm) -> dict:
    user = (f"Market: {market} (global view, {Y0}-{Y1})\n\n"
            f"Write a three-phase way forward for GLOBAL entry and scale-up in this market (the same layout as a "
            f"Middle East & Africa roadmap, but for the world market): phase 1 enter, phase 2 build, phase 3 scale. "
            f"No numbers except the phase windows, no company names, no Middle East or Africa specifics.\n"
            'Return {"phases": [{"name": "<2-4 words>", "window": "<e.g. ' + f"{Y0}-{Y0 + 1}" + '>", '
            '"actions": ["<3-6 words>", "<3-6 words>", "<3-6 words>"]}]}  -- exactly 3 phases')
    return llm.complete_json(_SYSTEM, user)


def _ask_regulation(market: str, m: dict, llm) -> dict:
    dfn = (m.get("definition") or {}).get("definition") or ""
    user = (f"Market: {market}\nDefinition: {dfn}\n\n"
            f"Describe the GLOBAL regulatory and policy landscape for this product outside the Middle East & Africa: "
            f"the main frameworks in the EU, the United States, and major Asia Pacific and Latin American markets. "
            f"Only frameworks that really exist; refer to each by its name WITHOUT regulation numbers or years.\n"
            'Return {"intro": "<2 sentences, no figures>", '
            '"frameworks": [{"jurisdiction": "...", "framework": "<official name, no numbers>", '
            '"what_it_means": "<1 sentence, no figures>"}]  (4-6 rows), '
            '"outlook": ["<4 one-sentence bullets on the global policy outlook to 2030, no figures>"]}')
    return llm.complete_json(_SYSTEM, user)


def _ask_watchlist(market: str, m: dict, llm) -> dict:
    dfn = (m.get("definition") or {}).get("definition") or ""
    user = (f"Market: {market}\nDefinition: {dfn}\n\n"
            f"A GLOBAL regulatory watchlist for {Y0}-{Y0 + 2}, outside the Middle East & Africa (EU, United States, "
            f"major Asia Pacific and Latin American markets): 4-6 regulatory or policy themes that are actually "
            f"under way or scheduled, what to watch, and the implication for this market. Name frameworks by name "
            f"without regulation numbers; no figures; nothing about the Middle East or Africa.\n"
            'Return {"watchlist": [{"theme": "<2-5 words>", "what_to_watch": "<1 sentence>", '
            '"implication": "<1 sentence>"}]}')
    return llm.complete_json(_SYSTEM, user)


def global_phases(raw: dict) -> list[dict]:
    ph = [p for p in (raw or {}).get("phases") or [] if p.get("name") and p.get("actions")]
    return ph[:3] if len(ph) >= 3 else []


def global_regulation(raw: dict, search=None, log: list | None = None) -> dict:
    """Frameworks kept only when a web search names them (as in resources.verified)."""
    from AiAgentsFleet.cmi_report import resources
    log = log if log is not None else []
    rows = []
    for r in (raw or {}).get("frameworks") or []:
        name = str(r.get("framework") or "").strip()
        if not (name and r.get("jurisdiction") and r.get("what_it_means")):
            continue
        ok = resources.verified(name, "", search or resources._default_search, strict=False)
        if ok is False:
            log.append(f"global regulation: '{name}' left out (not found by web search)")
            continue
        rows.append([str(r["jurisdiction"]), name, str(r["what_it_means"])])
    return {"intro": str((raw or {}).get("intro") or ""), "frameworks": rows,
            "outlook": [str(x) for x in (raw or {}).get("outlook") or [] if x][:4], "log": log}


def ensure(market_dir: Path, market: str, fleet: dict, m: dict, llm, refresh: bool = False) -> dict:
    """The raw answers, asked once and cached (report_extras.json)."""
    path = market_dir / "report_extras.json"
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    asks = (("mix", lambda: _ask_mix(market, m, llm)), ("tiers", lambda: _ask_tiers(market, fleet, llm)),
            ("tier_names", lambda: _ask_tier_names(out.get("tiers"), llm)),       # after 'tiers'
            ("matrix", lambda: _ask_matrix(market, m, llm)),
            ("stakeholders", lambda: _ask_stakeholders(market, llm)),
            ("phases", lambda: _ask_phases(market, llm)),
            ("country_mix", lambda: _ask_country_mix(market, m, llm)),
            ("watchlist", lambda: _ask_watchlist(market, m, llm)),
            ("regulation", lambda: _ask_regulation(market, m, llm)))
    if (old.get("version") == VERSION and not refresh and all(k in old for k, _ in asks)
            and "regulation_checked" in old):
        return old
    # only the missing answers are asked (a new section does not re-ask the cached ones)
    out = {**({} if refresh else old), "version": VERSION}
    for key, fn in asks:
        if key in out and "error" not in out[key]:
            continue
        try:
            out[key] = fn()
        except Exception as exc:                      # a failed ask leaves its section out, not the report
            out[key] = {"error": str(exc)}
        if key == "regulation":
            out.pop("regulation_checked", None)
    if "regulation_checked" not in out:              # the frameworks' web check, run once
        out["regulation_checked"] = global_regulation(out.get("regulation"))
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def derive(m: dict, raw: dict) -> dict:
    """What the charts and the builder use."""
    rmix = regional_mix(m, raw.get("mix"))
    segs = region_segments(m, {d: {r: v for r, v in rows.items() if r in OTHER_REGIONS} for d, rows in rmix.items()})
    return {"region_segments": segs, "country_mix": country_mix(m, raw.get("country_mix"), segs),
            "global_phases": global_phases(raw.get("phases")),
            "global_watchlist": [r for r in (raw.get("watchlist") or {}).get("watchlist") or []
                                 if isinstance(r, dict) and r.get("theme")][:6],
            "global_regulation": raw.get("regulation_checked") or {},
            "region_mix": rmix, "tiers": vendor_tiers(raw.get("tiers"), raw.get("tier_names")),
            "global_matrix": global_matrix(m, raw.get("matrix")),
            "global_stakeholders": [r for r in (raw.get("stakeholders") or {}).get("stakeholders") or []
                                    if r.get("stakeholder") and r.get("priority_moves") and r.get("kpis")]}
