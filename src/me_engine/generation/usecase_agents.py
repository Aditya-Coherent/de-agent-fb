"""Multi-agent assumptors for the bottom-up build: one specialist agent per
assumption instead of one agent guessing everything for a use case.

    scope agent      once     what is counted -- first fit on new machines only (an OEM /
                              original-equipment market) or new builds + replacements -- and where
                              demand is booked: the machine builder's location or the installation site
    geography agent  once     per use case, the country driver that tracks where demand is booked
    fleet agent      per use case x sample   size classes of the machine and new machines built
                              per year in each (installed base and product life when replacements count)
    fit agent        per use case x sample   per size class: share of those machines that use the
                              product as a separate unit, and units per machine
    price agent      per use case x sample   per size class: the product's price to the machine
                              builder and the complete machine's price
    critic (code)    bounds, product price <= half the machine's price, circular "market / price"
                     reasoning; one retry of the agent concerned with code's feedback

Code multiplies everything. Each sample's size classes are reduced to the builder's
structured inputs (new machines, unit-weighted fit share, units per machine, unit-weighted
price); the median of each input over the samples is used. The result has the same shape as
usecase_builder.build, so roll-up, ledger, Data-file writer, mix and demo page work unchanged.
Agents run in parallel (use cases x samples) to keep a market well under ten minutes.
"""
from __future__ import annotations

import json
import math
import re
import statistics
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from . import assumptor_regulator as ar
from . import usecase_builder as ub

N_SAMPLES = 3
WORKERS = 8
MAX_PRICE_SHARE_OF_MACHINE = 0.5   # a component costs at most half of the complete machine it goes into
SEARCH_WORKERS = 3                 # web search throttles under load
# where machine builders (OEMs) build machines: the only drivers offered when demand is booked at the builder
_OEM_DRIVERS = ("machinery_manufacturing_usd", "medhigh_tech_manufacturing_usd", "manufacturing_value_added_usd")
PRICE_METHODS = ("direct", "per_kw", "machine_share")
MAX_METHOD_SPREAD = 3.0            # a class's price methods may disagree by at most 3x before a retry


def _json(llm: Any, system: str, user: str) -> dict[str, Any]:
    out = llm.complete_json(system, user)
    return out if isinstance(out, dict) else {}


def _num(x: Any) -> float | None:
    try:
        v = float(x.get("value") if isinstance(x, dict) else x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _tag(sample: int, n: int, salt: str) -> str:
    # varies the prompt so the LLM cache (keyed by prompt) returns an independent answer
    return f"\nThis is independent estimate #{sample} of {n}" + (f" (run {salt})" if salt else "") + \
        ": reason it through from scratch.\n"


# ---- scope agent ------------------------------------------------------------------------------
_SCOPE_SYSTEM = (
    "You decide WHAT a market counts, before anyone sizes it. Read the market's name and taxonomy.\n"
    "1. count_replacements: does the market count only units fitted to NEWLY BUILT machines (first fit), or also "
    "units bought later to replace worn ones? A market named 'OEM' or 'original equipment' sells to machine "
    "builders and counts first fit on new machines only (replacements are the aftermarket), unless its definition "
    "says otherwise. A market of a consumable or an end-user market counts all purchases.\n"
    "2. demand_location: where is a unit's demand booked? 'machine_builder' -- the country where the machine "
    "builder (OEM) buys it and builds the machine, the natural basis for an OEM market; or 'installation' -- the "
    "country where the machine is operated, the natural basis for end-user and aftermarket markets.\n"
    "3. product_unit: one unit of the product, in plain words; and whether it is a separate unit bought by the "
    "machine builder. Return json.")


def scope(llm: Any, market: str, nesting: dict[str, Any]) -> dict[str, Any]:
    tax = {d: tr.get("__root__") for d, tr in nesting.items()}
    data = _json(llm, _SCOPE_SYSTEM,
                 f"Market: {market}\nTaxonomy: {json.dumps(tax, ensure_ascii=False)}\n"
                 'Return JSON: {"count_replacements": <true|false>, "demand_location": "<machine_builder | '
                 'installation>", "product_unit": "<one unit of the product>", "reason": "<2 sentences>"}')
    loc = str(data.get("demand_location") or "").strip().lower()
    return {"count_replacements": data.get("count_replacements") is not False,
            "demand_location": loc if loc in ("machine_builder", "installation") else "installation",
            "product_unit": str(data.get("product_unit") or "unit"), "reason": str(data.get("reason") or "")[:600]}


# ---- geography agent -----------------------------------------------------------------------------
def geography(llm: Any, market: str, ucs: list[dict[str, str]], sc: dict[str, Any],
              table: ub.DriverTable) -> dict[str, dict[str, str]]:
    where = ("where the machine builders (OEMs) that buy the product build their machines -- machine-building "
             "industry, not where the machines later run" if sc["demand_location"] == "machine_builder"
             else "where the machines are installed and operated")
    # the scope decides which drivers are admissible: an operation proxy (CO2, rents) cannot locate machine builders
    menu = ({k: ub.MENU[k] for k in _OEM_DRIVERS if k in ub.MENU} if sc["demand_location"] == "machine_builder"
            else ub.MENU)
    data = _json(llm,
                 "For each use case of a market, pick the ONE country-level driver from the menu whose size across "
                 f"countries best tracks {where}. The driver is used only to split a world quantity across "
                 "countries in proportion. Return json.",
                 f"Market: {market}\nUse cases: {json.dumps([u['use_case'] for u in ucs], ensure_ascii=False)}\n"
                 f"Menu: {json.dumps(menu)}\n"
                 'Return JSON: {"<use case>": {"driver": "<menu key>", "reason": "<one sentence>"}}')
    fallback = next(iter(menu)) if sc["demand_location"] == "machine_builder" else "industry_value_added_usd"
    out = {}
    for u in ucs:
        d = data.get(u["use_case"]) if isinstance(data.get(u["use_case"]), dict) else {}
        key = str(d.get("driver") or "")
        ok = key in menu and table.valid(key)
        out[u["use_case"]] = {"driver": key if ok else fallback,
                              "reason": str(d.get("reason") or "")[:300] if ok else f"fallback ({where})"}
    return out


# ---- per-use-case agents ------------------------------------------------------------------------------
_EVIDENCE_RULE = ("Every number needs a reason and a basis: an evidence id (e.g. 'E3') if the evidence states or "
                  "directly supports it, else 'assumption'. Never obtain a count by dividing a market's US$ size by a "
                  "price. Give plain numbers; code does all arithmetic.")


def _ev_text(evidence: list[dict[str, str]]) -> str:
    return "\n".join(f"[{e['id']}] ({e['source']}) {e['text']}" for e in evidence) or "(no evidence found)"


def fleet(llm: Any, market: str, uc: dict[str, str], sc: dict[str, Any], evidence: list[dict[str, str]],
          tag: str, feedback: str = "") -> dict[str, Any]:
    rep = ("" if not sc["count_replacements"] else
           ' and for each class "installed_machines" (in operation worldwide) and "product_life_years" (years one '
           "unit of the product serves before it is replaced, 5-40)")
    users = sc.get("end_users") or []
    scope_line = (f"The market covers ONLY machines serving these end-user industries (its taxonomy): "
                  f"{json.dumps(users, ensure_ascii=False)}. Machines built for any other industry (building services "
                  "and HVAC, consumer goods, vehicles, ...) are out of scope -- leave them out entirely; tag each class "
                  "with the listed industries it serves.\n" if users else "")
    return _json(llm,
                 "You are the FLEET agent. You count MACHINES -- individual pieces of equipment of one use case "
                 "(one turbine, one compressor, one pump), never plants, sites or projects. Split the machine into "
                 "2-4 size classes (by power, flow or speed) that differ in whether and how they use the product, "
                 "and give the number of NEW machines of each class built worldwide per year (all builders, every "
                 "in-scope end-user industry)" + rep + ". A forecast or a multi-year total (\"X units by 2031\", "
                 "\"expected to reach\") is not a yearly count. " + _EVIDENCE_RULE + " Return json.",
                 f"Market: {market}\nUse case (the machine): {uc['use_case']} ({uc['industry']})\n"
                 f"Product: {sc['product_unit']}\n{scope_line}Evidence:\n{_ev_text(evidence)}\n" + tag
                 + (f"\nREVIEW OF YOUR PREVIOUS ANSWER: {feedback}\n" if feedback else "")
                 + '\nReturn JSON: {"machine": "<what one machine is>", "classes": [{"class": "<name and size range>", '
                   '"serves": ["<listed end-user industries>"], '
                   '"new_machines_per_year": {"value": <n>, "basis": "<E-id or assumption>", "reason": "<why>"}'
                 + (', "installed_machines": {"value": <n>, "basis": "...", "reason": "..."}, "product_life_years": '
                    '{"value": <years>, "basis": "...", "reason": "..."}' if sc["count_replacements"] else "")
                 + '}]}')


def fit(llm: Any, market: str, uc: dict[str, str], sc: dict[str, Any], classes: list[dict[str, Any]],
        evidence: list[dict[str, str]], tag: str, feedback: str = "") -> dict[str, Any]:
    names = [c.get("class") for c in classes]
    return _json(llm,
                 "You are the FIT agent. For each size class of a machine, give the share of NEW machines of that "
                 "class whose drive train uses the product as a SEPARATE unit, and how many units one such machine "
                 "uses (1-4). Reason physically: such a unit is needed only where the machine's operating speed or "
                 "torque must be converted and no other arrangement (direct drive, integral gearing, a different "
                 "transmission type, variable-speed motor) does the job; for machine types built in large numbers "
                 "this is often a small niche, so do not assume most machines use it. Decide each class on its own "
                 "physics -- do not reuse a fixed pattern of shares across classes. If the product converts speed, give "
                 "for each class the machine's typical operating speed and the speed of the equipment on the other side "
                 "of the product (the driver or the driven machine) in rpm: where they are about equal the two can be "
                 "coupled directly and the product is not needed. " + _EVIDENCE_RULE + " Return json.",
                 f"Market: {market}\nUse case (the machine): {uc['use_case']}\nProduct: {sc['product_unit']}\n"
                 f"Size classes: {json.dumps(names, ensure_ascii=False)}\nEvidence:\n{_ev_text(evidence)}\n" + tag
                 + (f"\nREVIEW OF YOUR PREVIOUS ANSWER: {feedback}\n" if feedback else "")
                 + '\nReturn JSON: {"classes": [{"class": "<name as given>", "machine_speed_rpm": <number or null>, '
                   '"other_side_speed_rpm": <number or null>, "adoption_share": {"value": <0-1>, '
                   '"basis": "<E-id or assumption>", "reason": "<why>"}, "units_per_machine": {"value": <1-4>, '
                   '"basis": "...", "reason": "..."}}]}')


def price_queries(uc: dict[str, str], sc: dict[str, Any], term: str) -> list[str]:
    """Price evidence, searched separately from the machine-fleet evidence."""
    u = uc["use_case"]
    return [f"{term} for {u} price US$ per unit",
            f"{u} gearbox cost per kW",
            f"{u} price per unit US$ cost of complete package"]


_PRICE_METHOD_PROMPTS = {
    "direct": ("the product's own price: what the machine builder pays for ONE unit (ex-works, US$), from the price "
               "evidence or quotes and catalogues you know",
               '"price_usd": {"value": <US$ per unit>, "basis": "<E-id or assumption>", "reason": "<why>"}'),
    "per_kw": ("engineering cost: the rated power ONE unit of the product transmits in that class (kW) and the "
               "product's typical price per kW of rated power (US$/kW). Do not state a total price -- code multiplies",
               '"rated_kw": {"value": <kW>, "basis": "...", "reason": "..."}, '
               '"usd_per_kw": {"value": <US$ per kW>, "basis": "...", "reason": "..."}'),
    "machine_share": ("the complete machine: the price of one complete machine of that class (US$) and the share of "
                      "it the product represents (0-0.5). Do not state the product's price -- code multiplies",
                      '"machine_price_usd": {"value": <US$>, "basis": "...", "reason": "..."}, '
                      '"product_share_of_machine": {"value": <0-0.5>, "basis": "...", "reason": "..."}'),
}


def price_method(llm: Any, method: str, market: str, uc: dict[str, str], sc: dict[str, Any], names: list[str],
                 evidence: list[dict[str, str]], tag: str, feedback: str = "") -> dict[str, Any]:
    """One pricing method only: the agent never sees the other methods' numbers."""
    what, fields = _PRICE_METHOD_PROMPTS[method]
    return _json(llm,
                 f"You are a PRICE agent using ONE method: {what}. Answer for each size class of the machine. "
                 + _EVIDENCE_RULE + " Return json.",
                 f"Market: {market}\nUse case (the machine): {uc['use_case']}\nProduct: {sc['product_unit']}\n"
                 f"Size classes: {json.dumps(names, ensure_ascii=False)}\nPrice evidence:\n{_ev_text(evidence)}\n" + tag
                 + (f"\nREVIEW OF YOUR PREVIOUS ANSWER: {feedback}\n" if feedback else "")
                 + '\nReturn JSON: {"classes": [{"class": "<name as given>", ' + fields + "}]}")


def price(llm: Any, market: str, uc: dict[str, str], sc: dict[str, Any], classes: list[dict[str, Any]],
          evidence: list[dict[str, str]], tag: str, feedback: str = "") -> dict[str, Any]:
    """Three independent pricing agents in parallel (direct, per kW, share of the
    machine); their answers are merged per class and code compares them."""
    names = [c.get("class") for c in classes]
    with ThreadPoolExecutor(max_workers=len(PRICE_METHODS)) as pool:
        futs = {m: pool.submit(price_method, llm, m, market, uc, sc, names, evidence, tag, feedback)
                for m in PRICE_METHODS}
        answers = {m: f.result() for m, f in futs.items()}
    merged = []
    for i, n in enumerate(names):
        row: dict[str, Any] = {"class": n}
        for m in PRICE_METHODS:
            got = _by_class(answers[m].get("classes") if isinstance(answers[m].get("classes"), list) else [], names)
            if got[i]:
                row.update({k: v for k, v in got[i].items() if k != "class"})
        merged.append(row)
    return {"classes": merged, "by_method": answers}


def price_methods(r: dict[str, Any]) -> dict[str, float]:
    """The class's price by each method that has usable inputs (computed by code)."""
    out = {}
    direct = _num(r.get("price_usd"))
    if direct and direct > 0:
        out["direct"] = direct
    kw, per = _num(r.get("rated_kw")), _num(r.get("usd_per_kw"))
    if kw and per and kw > 0 and per > 0:
        out["per_kw"] = kw * per
    mp, sh = _num(r.get("machine_price_usd")), _num(r.get("product_share_of_machine"))
    if mp and sh and mp > 0 and 0 < sh <= MAX_PRICE_SHARE_OF_MACHINE:
        out["machine_share"] = mp * sh
    return out


def class_price(r: dict[str, Any]) -> float:
    m = price_methods(r)
    return statistics.median(m.values()) if m else 0.0


# ---- critic (code) and reduction -------------------------------------------------------------------
def _by_class(rows: list[dict[str, Any]], names: list[str]) -> list[dict[str, Any] | None]:
    norm = {str(r.get("class") or "").strip().lower(): r for r in rows if isinstance(r, dict)}
    out = [norm.get(str(n or "").strip().lower()) for n in names]
    if any(o is None for o in out) and len(rows) == len(names):  # renamed: fall back to order
        out = [o if o is not None else rows[i] for i, o in enumerate(out)]
    return out


def critique_fleet(f: dict[str, Any], sc: dict[str, Any]) -> list[str]:
    cl = f.get("classes") if isinstance(f.get("classes"), list) else []
    if not 1 <= len(cl) <= 5:
        return ["give 2-4 size classes"]
    probs = []
    for c in cl:
        n = _num(c.get("new_machines_per_year"))
        if n is None or n <= 0:
            probs.append(f"class '{c.get('class')}': new_machines_per_year must be a positive number")
        if sc["count_replacements"]:
            life, inst = _num(c.get("installed_machines")), _num(c.get("product_life_years"))
            if inst is None or inst <= 0:
                probs.append(f"class '{c.get('class')}': installed_machines must be positive")
            if life is None or not 5 <= life <= 40:
                probs.append(f"class '{c.get('class')}': product_life_years must be 5-40")
        if ub.CIRCULAR.search(json.dumps(c)):
            probs.append(f"class '{c.get('class')}': a count was derived from a market size divided by a price")
        nreason = str((c.get("new_machines_per_year") or {}).get("reason") or "") \
            if isinstance(c.get("new_machines_per_year"), dict) else ""
        if FORECAST.search(nreason):
            probs.append(f"class '{c.get('class')}': new_machines_per_year rests on a forecast or multi-year total "
                         "-- give the number built in one year")
        users = sc.get("end_users") or []
        if users:
            serves = [str(x) for x in (c.get("serves") or []) if str(x).strip()]
            if not serves or not any(_in_scope(x, users) for x in serves):
                probs.append(f"class '{c.get('class')}': serves none of the market's end-user industries "
                             f"{users} -- drop it or count only its in-scope machines")
    return probs


# a projection or multi-year total used as a yearly machine count
FORECAST = re.compile(r"(expected to (reach|grow)|forecast|projected|by 20[2-4]\d|cumulative|over the next \d+ years|"
                      r"\b\d+\s*/\s*\d+\s*years?\b)", re.I)


def _in_scope(name: str, users: list[str]) -> bool:
    n = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    for u in users:
        words = [w for w in re.sub(r"[^a-z0-9 ]", " ", u.lower()).split() if len(w) > 2 and w not in ("and", "others")]
        if words and any(w in n.split() or w in n for w in words):
            return True
    return False


def critique_fit(ft: dict[str, Any], names: list[str]) -> list[str]:
    rows = _by_class(ft.get("classes") if isinstance(ft.get("classes"), list) else [], names)
    probs = []
    for n, r in zip(names, rows):
        if r is None:
            probs.append(f"class '{n}' missing")
            continue
        a, u = _num(r.get("adoption_share")), _num(r.get("units_per_machine"))
        if a is None or not 0 <= a <= 1:
            probs.append(f"class '{n}': adoption_share must be 0-1")
        if u is None or not 1 <= u <= 4:
            probs.append(f"class '{n}': units_per_machine must be 1-4")
        s1, s2 = _num(r.get("machine_speed_rpm")), _num(r.get("other_side_speed_rpm"))
        if a is not None and s1 and s2 and s1 > 0 and s2 > 0:
            ratio = max(s1, s2) / min(s1, s2)
            if ratio < SPEED_MATCH and a > 0.25:
                probs.append(f"class '{n}': the machine ({s1:,.0f} rpm) and the other side ({s2:,.0f} rpm) run at about "
                             f"the same speed, so they can be coupled directly -- yet adoption is {a:.0%}; re-check")
    vals = [round(_num(r.get("adoption_share")) or -1, 3) for r in rows if r]
    if len(vals) >= 3 and len(set(vals)) == len(vals) and vals == sorted(vals) and \
            all(math.isclose(vals[i + 1] - vals[i], vals[1] - vals[0]) for i in range(len(vals) - 1)):
        probs.append("adoption rises by the same step in every class -- that is a pattern, not physics; decide each "
                     "class on its speeds and drive arrangement")
    return probs


SPEED_MATCH = 1.15   # speeds within 15% of each other: direct coupling is possible


def critique_price(pr: dict[str, Any], names: list[str]) -> list[str]:
    rows = _by_class(pr.get("classes") if isinstance(pr.get("classes"), list) else [], names)
    probs = []
    for n, r in zip(names, rows):
        if r is None:
            probs.append(f"class '{n}' missing")
            continue
        meth = price_methods(r)
        if len(meth) < 2:
            probs.append(f"class '{n}': give at least two of the three price methods with positive numbers "
                         "(product_share_of_machine at most 0.5)")
            continue
        lo, hi = min(meth.values()), max(meth.values())
        if hi > MAX_METHOD_SPREAD * lo:
            probs.append(f"class '{n}': your methods disagree by more than {MAX_METHOD_SPREAD:g}x ("
                         + ", ".join(f"{k} ${v:,.0f}" for k, v in meth.items()) + ") -- re-check the one that is off")
        m = _num(r.get("machine_price_usd"))
        if m and statistics.median(meth.values()) > MAX_PRICE_SHARE_OF_MACHINE * m:
            probs.append(f"class '{n}': the product (${statistics.median(meth.values()):,.0f}) costs more than "
                         f"{MAX_PRICE_SHARE_OF_MACHINE:.0%} of the complete machine (${m:,.0f})")
    return probs


def reduce_sample(f: dict[str, Any], ft: dict[str, Any], pr: dict[str, Any], sc: dict[str, Any]) -> dict[str, Any]:
    """Size classes -> the builder's structured inputs (computed by code)."""
    cl = f["classes"]
    names = [c.get("class") for c in cl]
    fits, prices = _by_class(ft["classes"], names), _by_class(pr["classes"], names)
    rows = []
    for c, fr, prr in zip(cl, fits, prices):
        new = _num(c.get("new_machines_per_year")) or 0.0
        flow = new + ((_num(c.get("installed_machines")) or 0.0) / (_num(c.get("product_life_years")) or 1.0)
                      if sc["count_replacements"] else 0.0)
        a, u = _num(fr.get("adoption_share")) or 0.0, _num(fr.get("units_per_machine")) or 1.0
        rows.append({"class": c.get("class"), "new": new, "flow": flow, "adoption": a, "per_machine": u,
                     "units": flow * a * u, "price": class_price(prr), "price_methods": price_methods(prr),
                     "machine_price": _num(prr.get("machine_price_usd")),
                     "inst": _num(c.get("installed_machines")), "life": _num(c.get("product_life_years")),
                     # every input with the agent's own reason and basis, for the audit trail
                     "why": {k: _why(src.get(k)) for src, ks in
                             ((c, ("new_machines_per_year", "installed_machines", "product_life_years")),
                              (fr, ("adoption_share", "units_per_machine")),
                              (prr, ("price_usd", "rated_kw", "usd_per_kw", "machine_price_usd",
                                     "product_share_of_machine"))) for k in ks if src.get(k) is not None}})
    new = math.fsum(r["new"] for r in rows)
    flow = math.fsum(r["flow"] for r in rows)
    units = math.fsum(r["units"] for r in rows)
    adopters = math.fsum(r["flow"] * r["adoption"] for r in rows)
    out = {"classes": rows, "units": units,
           "new_machines_per_year": new,
           "adoption_share": adopters / flow if flow else 0.0,
           "units_per_machine": units / adopters if adopters else 1.0,
           "price": math.fsum(r["units"] * r["price"] for r in rows) / units if units else 0.0}
    if sc["count_replacements"]:
        inst = math.fsum(r["inst"] or 0.0 for r in rows)
        out["installed_machines"] = inst
        out["product_life_years"] = inst / (flow - new) if flow > new else 20.0
        out["adoption_share"] = adopters / flow if flow else 0.0
    return out


def _why(entry: Any) -> dict[str, Any]:
    e = entry if isinstance(entry, dict) else {"value": entry}
    return {"value": _num(e), "basis": str(e.get("basis") or "assumption"), "reason": str(e.get("reason") or "")[:400]}


def _basis(entries: list[Any], evidence: list[dict[str, str]]) -> str:
    ids = {e["id"] for e in evidence}
    cited = [i for x in entries if isinstance(x, dict) for i in re.findall(r"E\d+", str(x.get("basis") or ""))
             if i in ids]
    return " ".join(dict.fromkeys(cited)) if cited and len(cited) >= len(entries) else "assumption"


# ---- one sample: fleet -> fit -> price, each with one critic retry ----------------------------------
def run_sample(llm: Any, market: str, uc: dict[str, str], sc: dict[str, Any], evidence: list[dict[str, str]],
               k: int, n: int, salt: str, price_evidence: list[dict[str, str]] | None = None) -> dict[str, Any] | None:
    """fleet first; then the fit and price agents in parallel (both need only the size classes)."""
    tag = _tag(k, n, salt)
    pev = evidence if price_evidence is None else price_evidence
    log: list[str] = []

    def ask(name: str, agent: Callable[[str], dict[str, Any]], check: Callable[[dict[str, Any]], list[str]]
            ) -> tuple[dict[str, Any], list[str]]:
        out = agent("")
        probs = check(out)
        if probs:  # one retry with code's findings
            fb = "Code found: " + "; ".join(probs)
            log.append(f"{name}: {fb}")
            out = agent(fb)
            probs = check(out)
        return out, probs

    try:
        f, pf = ask("fleet", lambda fb: fleet(llm, market, uc, sc, evidence, tag, fb), lambda o: critique_fleet(o, sc))
        if not (isinstance(f.get("classes"), list) and f["classes"]):
            return None
        names = [c.get("class") for c in f["classes"]]
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_fit = pool.submit(ask, "fit", lambda fb: fit(llm, market, uc, sc, f["classes"], evidence, tag, fb),
                                lambda o: critique_fit(o, names))
            f_price = pool.submit(ask, "price", lambda fb: price(llm, market, uc, sc, f["classes"], pev, tag, fb),
                                  lambda o: critique_price(o, names))
            (ft, pfit), (pr, pprice) = f_fit.result(), f_price.result()
        red = reduce_sample(f, ft, pr, sc)
    except Exception as exc:  # noqa: BLE001
        log.append(f"sample failed: {exc}")
        return None
    if not (red["units"] > 0 and red["price"] > 0):
        return None
    return {"sample": k, "fleet": f, "fit": ft, "price_agent": pr, "reduced": red, "machine": f.get("machine"),
            "remaining_problems": pf + pfit + pprice, "feedback": log,
            "basis": {"new_machines_per_year": _basis([c.get("new_machines_per_year") for c in f["classes"]], evidence),
                      "adoption_share": _basis([r.get("adoption_share") for r in ft.get("classes") or []], evidence),
                      "units_per_machine": _basis([r.get("units_per_machine") for r in ft.get("classes") or []], evidence),
                      "price": _basis([r.get("price_usd") for r in pr.get("classes") or []], pev)}}


def combine(samples: list[dict[str, Any]], sc: dict[str, Any], driver: str, machine: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The MEDIAN SAMPLE (by value = units x price) supplies every input and the price
    together, so its inputs stay one consistent design; the other samples are kept
    as the spread. (A median taken input by input mixes samples -- seen: a machine
    count from one sample with the adoption of another.)"""
    keys = list(ub.FIRST_FIT_INPUTS) + (["installed_machines", "product_life_years"] if sc["count_replacements"] else [])
    ranked = sorted(samples, key=lambda s: s["reduced"]["units"] * s["reduced"]["price"])
    med = ranked[(len(ranked) - 1) // 2]
    world, spread = {}, {}
    for key in keys:
        world[key] = {"value": med["reduced"][key],
                      "unit": {"adoption_share": "share", "units_per_machine": "units/machine",
                               "product_life_years": "years"}.get(key, "machines"),
                      "basis": med["basis"].get(key, "assumption"),
                      "reason": f"sample {med['sample']} (the median of {len(samples)} by value), summed over size "
                                "classes: " + "; ".join(f"{c['class']}" for c in med["reduced"]["classes"])}
        spread[key] = ub._spread([s["reduced"][key] for s in samples])
    spread["price"] = ub._spread([s["reduced"]["price"] for s in samples])
    spread["world_units_per_sample"] = ub._spread([s["reduced"]["units"] for s in samples])
    spread["value_per_sample_mn"] = ub._spread([s["reduced"]["units"] * s["reduced"]["price"] / 1e6 for s in samples])
    spec = {"archetype": "durable", "driver": driver, "machine": machine, "quantity_unit": "units",
            "units_label": sc["product_unit"], "count_replacements": sc["count_replacements"], "world": world,
            "price_usd_per_unit": {"value": med["reduced"]["price"], "per": "unit", "basis": med["basis"]["price"],
                                   "reason": f"sample {med['sample']}: unit-weighted over size classes of the median "
                                             "of three independent pricing methods (ex-works to the machine builder)"}}
    return spec, {"spread": spread, "used_samples": [med["sample"]],
                  "excluded_samples": [], "driver_votes": [driver], "median_sample": med["sample"]}


_END_USER_HINTS = ("vertical", "end user", "end-user", "end use", "industry")


def end_users(nesting: dict[str, Any], partition: str | None) -> list[str]:
    """Segments of the taxonomy's end-user dimension(s) other than the use-case partition."""
    out: list[str] = []
    for d, tree in nesting.items():
        if d == partition or not any(h in d.lower() for h in _END_USER_HINTS):
            continue
        for p, kids in tree.items():
            for s in ([] if p == "__root__" else [p]) + list(kids):
                if s not in out:
                    out.append(s)
    return out


# ---- the build ----------------------------------------------------------------------------------------
def build(llm: Any, spec: dict[str, Any], nesting: dict[str, Any], market: str, cache_dir: Path,
          search: Callable[[list[str]], list[Any]], published_by_industry: dict[str, list[dict[str, Any]]],
          say: Callable[[str], None] = print, *, n_samples: int = N_SAMPLES, salt: str = "",
          blocklist: tuple[str, ...] | list[str] = ub.DEFAULT_BLOCKLIST, workers: int = WORKERS,
          fao_cache: dict[str, Any] | None = None) -> dict[str, Any]:
    dim, ucs = ub.use_cases(nesting)
    geos = spec["geographies"]
    table = ub.DriverTable(ub.load_drivers(cache_dir), geos,
                           ub._load_fao_cache() if fao_cache is None else fao_cache)
    term = market.replace("Global", "").replace("Market", "").strip()

    with ThreadPoolExecutor(max_workers=SEARCH_WORKERS + 2) as pool:
        f_scope = pool.submit(scope, llm, market, nesting)
        f_search = {u["use_case"]: pool.submit(search, ub._search_queries(u, term)) for u in ucs}
        sc = f_scope.result()
        # the market's end users = every segment of its end-user dimensions (the taxonomy, labels only)
        sc["end_users"] = end_users(nesting, dim)
        f_geo = pool.submit(geography, llm, market, ucs, sc, table)
        f_price = {u["use_case"]: pool.submit(search, price_queries(u, sc, term)) for u in ucs}

        def snippets_of(fut: Any) -> tuple[list[Any], int]:
            try:
                return ub.filter_snippets(fut.result() or [], blocklist)
            except Exception:  # noqa: BLE001 -- no evidence: the agents label their inputs as assumptions
                return [], 0

        evid, pevid, blocked = {}, {}, {}
        for u in ucs:
            fleet_snips, n1 = snippets_of(f_search[u["use_case"]])
            price_snips, n2 = snippets_of(f_price[u["use_case"]])
            evid[u["use_case"]] = ub._evidence(fleet_snips)
            # price evidence numbered after the fleet evidence: one id space for the ledger's labels
            off = len(evid[u["use_case"]])
            pevid[u["use_case"]] = [{**e, "id": f"E{off + i}"} for i, e in enumerate(ub._evidence(price_snips), start=1)]
            blocked[u["use_case"]] = n1 + n2
        geo = f_geo.result()
    say(f"[agents] scope: count_replacements={sc['count_replacements']}, demand booked at {sc['demand_location']}, "
        f"product = {sc['product_unit']}")

    jobs = [(u, k) for u in ucs for k in range(1, n_samples + 1)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {(u["use_case"], k): pool.submit(run_sample, llm, market, u, sc, evid[u["use_case"]], k, n_samples, salt,
                                                pevid[u["use_case"]])
                for u, k in jobs}
        samples = {key: f.result() for key, f in futs.items()}

    results = []
    for u in ucs:
        got = [samples[(u["use_case"], k)] for k in range(1, n_samples + 1) if samples[(u["use_case"], k)]]
        if not got:
            say(f"[agents] {u['use_case']}: no usable sample")
            continue
        driver = geo[u["use_case"]]["driver"]
        machine = next((s["machine"] for s in got if s.get("machine")), u["use_case"])
        cspec, info = combine(got, sc, driver, str(machine))
        try:
            ev = ub._evaluate(cspec, table, geos)
        except Exception as exc:  # noqa: BLE001
            say(f"[agents] {u['use_case']}: combined design unusable ({exc})")
            continue
        flags = [f"sample {s['sample']}: still after retry -- " + "; ".join(s["remaining_problems"])
                 for s in got if s["remaining_problems"]]
        legacy = [{"sample": s["sample"], "attempts": 1 + len(s["feedback"]), "spec": cspec, "world_units": s["reduced"]["units"],
                   "price": s["reduced"]["price"], "violations": s["remaining_problems"], "circular": False,
                   "kept_out_of_bounds": bool(s["remaining_problems"]), "feedback": s["feedback"]} for s in got]
        item = ub._item(u, cspec, table, geos, ev, evidence=evid[u["use_case"]] + pevid[u["use_case"]], published=[], samples=legacy,
                        info=info, flags=flags, blocked={"snippets": blocked[u["use_case"]], "published_figures": 0})
        item["agents"] = {"scope": sc, "geography": geo[u["use_case"]],
                          "samples": [{"sample": s["sample"], "machine": s["machine"], "classes": s["reduced"]["classes"],
                                       "units": s["reduced"]["units"], "price": s["reduced"]["price"],
                                       "basis": s["basis"], "feedback": s["feedback"]} for s in got]}
        results.append(item)
        say(f"[agents] {u['use_case']}: {item['world_units']:,.0f} units/yr x ${item['price_per_quantity_unit']:,.0f} = "
            f"${item['world_mn']:,.1f}M via {driver}; units over samples "
            f"x{(info['spread']['world_units_per_sample']['max_over_min'] or 0):.2f}"
            + (f"; FLAGS: {' | '.join(flags)}" if flags else ""))

    out = {"dimension": dim, "use_cases": results, "flags": [], "base_year": ub.BASE_YEAR, "mode": "agents",
           "scope": sc, "settings": {"n_samples": n_samples, "salt": salt, "blocklist": list(blocklist),
                                     "workers": workers, "max_price_share_of_machine": MAX_PRICE_SHARE_OF_MACHINE},
           "drivers_used": {r["spec"]["driver"]: table.meta(r["spec"]["driver"]) for r in results}}
    return ub.rollup(out, spec)
