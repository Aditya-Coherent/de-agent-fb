"""Checks the desk-research agents' region and country splits, and re-asks
the same agent once when an answer is incoherent (PLAN-03). Nothing here
produces a number: a split is either the agent's own answer, or the agent's
second answer after it was shown what was wrong with the first.

- check_regions: the regional agent's five regions against the global headline.
- check_split / check_splits: one region's country split (named countries +
  'Rest of <region>').
- reask_note / review_splits: the one re-ask per country agent.
- sanity_prompt / resolve_flags: one sceptical review of the final country
  table, with a re-ask of the country agent for each flagged region.
- review: the final verdict recorded in the model as m["geo_review"]: what was
  checked, what was re-asked, what was normalised and what is still a blocker
  (PLAN-08 turns blockers into release-gate failures) or a warning.
"""
from __future__ import annotations

import json
from typing import Callable

from AiAgentsFleet.cmi_report.groups import MEMBERS, REST, canonical, region_of

SUM_LO, SUM_HI = 99.0, 101.0          # a split's shares, Rest of included
REG_LO, REG_HI = 0.90, 1.10           # regional values vs the global headline
SAME_REST_MIN = 3                     # identical Rest-of share in this many regions = a plug value
MAIN_REGIONS = ["North America", "Europe", "Asia Pacific", "Latin America", "Middle East & Africa"]
# which agent answers which region's split
GROUP_REGIONS = {"countries_main": ["North America", "Europe", "Asia Pacific", "Latin America"],
                 "countries_mea": ["Middle East", "Africa"]}
REGION_GROUP = {r: k for k, rs in GROUP_REGIONS.items() for r in rs}
# split problems the re-ask decides: still present after it -> warning, not blocker.
# Every regional problem left after its re-asks (sum aside) is a blocker.
SOFT = {"rest_too_small"}
REGIONS_ROW = "Global regions"        # the sanity table's region rows
BAL_OTHERS = 0.10                     # single-region balancing: every other region moved less than this,
BAL_FACTOR, BAL_SHARE = 2.0, 0.60     # while one moved more than 2x or took > 60% of the total change


def _num(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _problem(region: str, kind: str, detail: str) -> dict:
    return {"region": region, "kind": kind, "detail": detail}


def keyed(raw: dict | None) -> dict:
    """An answer keyed by canonical country names ('United Arab Emirates' -> 'UAE')."""
    return {canonical(k): v for k, v in (raw or {}).items()} if isinstance(raw, dict) else {}


def rest_share(region: str, answer: dict) -> float | None:
    rest = keyed(answer).get(REST.get(region, ""))
    return _num(rest.get("share_pct_of_region")) if isinstance(rest, dict) else None


# --------------------------------------------------------------------------- #
# checks
# --------------------------------------------------------------------------- #
def check_split(region: str, answer: dict | None, names: list[str] | None = None, *,
                elsewhere: dict[str, float] | None = None, region_value: float | None = None,
                other_rest: dict[str, float | None] | None = None) -> list[dict]:
    """Problems with one region's split ([] = coherent).

    `answer` is {country: {"share_pct_of_region", "cagr_2026_2033_pct"}} plus
    'Rest of <region>'; `names` the countries the report names there.
    `elsewhere` is {country: value} from the desk research's regional answer and
    `region_value` that region's value in the same answer -- used only to spot
    a Rest-of that is smaller than a named country although one of its own
    members is sized larger than that country. `other_rest` is the Rest-of
    share of the other regions (identical shares across regions = a plug)."""
    a = keyed(answer)
    names = names if names is not None else [k for k in a if not k.startswith("Rest of")]
    out: list[dict] = []
    shares: dict[str, float] = {}
    for c in names:
        s = _num((a.get(c) or {}).get("share_pct_of_region")) if isinstance(a.get(c), dict) else None
        if s is None or s <= 0:
            out.append(_problem(region, "nonpositive", f"{c}: share {s if s is not None else 'missing'}"))
        else:
            shares[c] = s
    rest = None
    if region in REST:
        rest = rest_share(region, a)
        if rest is None:
            out.append(_problem(region, "rest_missing", f"no '{REST[region]}' share given"))
        elif rest <= 0:
            out.append(_problem(region, "nonpositive", f"{REST[region]}: share {rest:g}"))
    total = sum(shares.values()) + (rest or 0)
    if not SUM_LO <= total <= SUM_HI:
        out.append(_problem(region, "sum", f"shares sum to {total:.1f}% (must be 100 +/-1, Rest of included)"))

    # Rest of < a named country, while one of its members is sized larger than that country
    if region in REST and rest is not None and rest > 0 and elsewhere:
        ew = {canonical(k): _num(v) for k, v in elsewhere.items()}
        members = [m for m in MEMBERS[region] if m not in names]
        for c, s in sorted(shares.items(), key=lambda kv: kv[1]):
            if s <= rest:
                continue
            c_val = ew.get(c) or (s / 100 * region_value if region_value else None)
            big = [m for m in members if c_val and (ew.get(m) or 0) > c_val]
            if big:
                out.append(_problem(region, "rest_too_small",
                                    f"{REST[region]} ({rest:g}%) is smaller than {c} ({s:g}%), yet it includes "
                                    f"{', '.join(big)}, which the desk research sizes larger than {c}"))
                break

    if other_rest and rest is not None:
        same = sorted(r for r, v in {**other_rest, region: rest}.items() if v is not None and round(v, 1) == round(rest, 1))
        if len(same) >= SAME_REST_MIN:
            out.append(_problem(region, "same_rest", f"'Rest of' is {rest:.1f}% in {len(same)} regions "
                                                    f"({', '.join(same)}) -- a plug value, not an estimate"))

    if region == "North America":
        g = {c: _num((a.get(c) or {}).get("cagr_2026_2033_pct")) for c in names if isinstance(a.get(c), dict)}
        vals = [v for v in g.values() if v is not None]
        if len(vals) >= 2 and len({round(v, 1) for v in vals}) == 1:
            out.append(_problem(region, "same_cagr", f"{' and '.join(g)} have the same CAGR ({vals[0]:.1f}%)"))
    return out


def splits_of(fields: dict) -> dict[str, dict]:
    """{region: that region's split} from the two country agents' answers."""
    out = {}
    for key, regions in GROUP_REGIONS.items():
        ans = keyed(fields.get(key))
        for r in regions:
            out[r] = ans.get(r) or {}
    return out


def check_splits(splits: dict[str, dict], geo: dict[str, list[str]], *,
                 elsewhere: dict[str, float] | None = None,
                 region_values: dict[str, float] | None = None) -> dict[str, list[dict]]:
    """check_split for every region, with the cross-region Rest-of check."""
    rests = {r: rest_share(r, a) for r, a in splits.items() if r in REST}
    return {r: check_split(r, a, geo.get(r), elsewhere=elsewhere, region_value=(region_values or {}).get(r),
                           other_rest={k: v for k, v in rests.items() if k != r})
            for r, a in splits.items()}


def check_regions(regional: dict | None, global_value: float) -> list[dict]:
    """The regional agent's answer ({"regions": {...}}) against the global headline."""
    regions = (regional or {}).get("regions") or {}
    out: list[dict] = []
    total = 0.0
    for r in MAIN_REGIONS:
        v = _num((regions.get(r) or {}).get("value_usd_mn"))
        if v is None or v <= 0:
            out.append(_problem(r, "nonpositive", f"{r}: value {v if v is not None else 'missing'}"))
        else:
            total += v
        for c in (regions.get(r) or {}).get("countries") or []:
            home = region_of(c.get("name") if isinstance(c, dict) else c)
            home = "Middle East & Africa" if home in ("Middle East", "Africa") else home
            if home and home != r:
                out.append(_problem(r, "convention", f"{canonical(c.get('name'))} is listed under {r}; "
                                                     f"it belongs to {home}"))
    if global_value and not REG_LO <= total / global_value <= REG_HI:
        out.append(_problem("Global", "sum", f"regions sum to ${total:,.1f}M, {total / global_value:.2f}x the "
                                             f"global ${global_value:,.1f}M (must be 0.90-1.10x)"))
    return out


def _values(regional: dict | None) -> dict[str, float]:
    regions = (regional or {}).get("regions") or {}
    return {r: _num((regions.get(r) or {}).get("value_usd_mn")) or 0.0 for r in MAIN_REGIONS}


def balancing(before: dict | None, after: dict | None) -> list[dict]:
    """A re-ask that closed the gap to the global headline by moving one region:
    that region moved by >= 10% and either more than 2x or more than 60% of the
    total change, while every other region moved less than 10%."""
    b, a = _values(before), _values(after)
    if not all(b.values()) or not all(a.values()):
        return []
    rel = {r: abs(a[r] / b[r] - 1) for r in MAIN_REGIONS}
    total = sum(abs(a[r] - b[r]) for r in MAIN_REGIONS)
    out = []
    for r in MAIN_REGIONS:
        factor = max(a[r] / b[r], b[r] / a[r])
        if (rel[r] >= BAL_OTHERS and all(rel[o] < BAL_OTHERS for o in MAIN_REGIONS if o != r)
                and (factor > BAL_FACTOR or abs(a[r] - b[r]) > BAL_SHARE * total)):
            out.append(_problem(r, "balancing",
                                f"single-region balancing: {r} moved from ${b[r]:,.1f}M to ${a[r]:,.1f}M "
                                f"({factor:.1f}x) while every other region moved less than 10%"))
    return out


def _valid_regions(answer) -> bool:
    regs = answer.get("regions") if isinstance(answer, dict) else None
    return isinstance(regs, dict) and all(_num((regs.get(r) or {}).get("value_usd_mn")) is not None and
                                          _num((regs.get(r) or {}).get("cagr_2025_2030_pct")) is not None
                                          for r in MAIN_REGIONS)


def _region_changes(before: dict | None, after: dict | None) -> list[str]:
    b, a = _values(before), _values(after)
    return [f"{r}: {b[r]:g} -> {a[r]:g}" for r in MAIN_REGIONS]


def _spread_note(probs: list[dict]) -> str:
    return " ".join(f"You closed the gap by inflating {p['region']} alone. The gap must be spread across the "
                    f"regions according to the evidence, not dumped into one region." for p in probs)


def elsewhere_values(regional: dict | None) -> tuple[dict[str, float], dict[str, float]]:
    """({country: value}, {region: value}) from the regional agent's answer;
    the Middle East and Africa each get the whole MEA value (the countries are
    compared in the same units)."""
    regions = (regional or {}).get("regions") or {}
    countries, values = {}, {}
    for r, d in regions.items():
        v = _num((d or {}).get("value_usd_mn"))
        for name in ([r] if r != "Middle East & Africa" else ["Middle East", "Africa"]):
            if v:
                values[name] = v
        for c in (d or {}).get("countries") or []:
            cv = _num(c.get("value_usd_mn")) if isinstance(c, dict) else None
            if cv:
                countries[canonical(c.get("name"))] = cv
    return countries, values


# --------------------------------------------------------------------------- #
# re-asks
# --------------------------------------------------------------------------- #
def reask_note(problems: list[dict], previous: dict, extra: str = "") -> str:
    """Appended to the agent's original prompt for its one re-ask."""
    prev = {k: v for k, v in (previous or {}).items() if k != "_sources"}
    return ("\n\nYOUR PREVIOUS ANSWER (below) failed these checks:\n"
            + "\n".join(f"- {p['region']}: {p['detail']}" for p in problems)
            + (f"\n{extra}" if extra else "")
            + "\nRe-examine the evidence and return a corrected answer in exactly the same shape. Fix the "
              "countries or regions that are wrong; do not just rescale every figure.\n"
            + f"PREVIOUS ANSWER:\n{json.dumps(prev, ensure_ascii=False)}")


def _shares(answer: dict, regions: list[str]) -> dict[str, float | None]:
    a = keyed(answer)
    return {f"{r}/{c}": _num((v or {}).get("share_pct_of_region")) for r in regions
            for c, v in keyed(a.get(r)).items() if isinstance(v, dict)}


def changes(before: dict, after: dict, regions: list[str]) -> list[str]:
    """'Europe/Rest of Europe: 12 -> 21.5' for every share the re-ask moved."""
    b, a = _shares(before, regions), _shares(after, regions)
    return [f"{k}: {b.get(k)} -> {a.get(k)}" for k in sorted(set(b) | set(a))
            if b.get(k) is None or a.get(k) is None or abs((b.get(k) or 0) - (a.get(k) or 0)) >= 0.05]


def reask_regions(regional: dict, global_value: float, ask: Callable[[str], dict | None],
                  log: list[str]) -> dict:
    """Checks the regional agent's answer; when it fails, asks once more
    (`ask(note)` returns the agent's new JSON). If that re-ask closed the gap
    by inflating one region (balancing), asks one more time. Returns the record
    kept in cmi_fields geo_review["regional"] (with the kept answer, if any)."""
    probs = check_regions(regional, global_value)
    rec: dict = {"problems": probs, "reasked": False, "global_value_usd_mn": global_value}
    if not probs:
        return rec
    log.append("[regional] " + "; ".join(p["detail"] for p in probs)[:400] + " -- re-asking once")
    rec["reasked"] = True
    extra = (f"The global market is ${global_value:,.1f}M in the base year (the market_size desk research); "
             f"the five regions are slices of it and must sum to it.")

    def ask_once(note: str) -> dict | None:
        try:
            again = ask(note)
        except Exception as exc:  # noqa: BLE001
            log.append(f"[regional] re-ask failed: {exc}")
            return None
        return {k: v for k, v in again.items() if k != "_sources"} if _valid_regions(again) else None

    answer = ask_once(reask_note(probs, regional, extra))
    if answer is None:
        rec["reask_failed"] = True
    else:
        bal = balancing(regional, answer)
        if bal:
            log.append("[regional] " + "; ".join(p["detail"] for p in bal) + " -- re-asking once more")
            rec["balancing"] = bal
            rec["balancing_answer"] = answer
            rec["reasked_balancing"] = True
            again = ask_once(reask_note(bal + check_regions(answer, global_value), answer,
                                        extra + " " + _spread_note(bal)))
            if again is not None:
                answer = again
        rec["answer"] = answer
        rec["changes"] = _region_changes(regional, answer)
    final = rec.get("answer") or regional
    rec["after"] = check_regions(final, global_value) + (balancing(regional, final) if rec.get("answer") else [])
    log.append(f"[regional] after re-ask: {len(rec['after'])} problem(s)")
    return rec


def review_splits(fields: dict, geo: dict, ask: Callable[[str, str], dict | None], log: list[str], *,
                  regional: dict | None = None) -> dict:
    """Checks both country agents' splits and re-asks each agent whose regions
    fail, once, keeping its second answer in `fields`. Returns
    {group key: record} for cmi_fields geo_review["splits"]."""
    ew, rv = elsewhere_values(regional)
    probs = check_splits(splits_of(fields), geo, elsewhere=ew, region_values=rv)
    out = {}
    for key, regions in GROUP_REGIONS.items():
        bad = [p for r in regions for p in probs.get(r, [])]
        rec: dict = {"problems": bad, "reasked": False}
        out[key] = rec
        if not bad or not isinstance(fields.get(key), dict) or "error" in fields[key]:
            continue
        log.append(f"[{key}] " + "; ".join(f"{p['region']}: {p['detail']}" for p in bad)[:400] + " -- re-asking once")
        rec["reasked"] = True
        try:
            again = ask(key, reask_note(bad, fields[key]))
        except Exception as exc:  # noqa: BLE001
            log.append(f"[{key}] re-ask failed: {exc}")
            again = None
        if isinstance(again, dict) and "error" not in again and any(r in keyed(again) for r in regions):
            rec["changes"] = changes(fields[key], again, regions)
            fields[key] = again
        else:
            rec["reask_failed"] = True
    after = check_splits(splits_of(fields), geo, elsewhere=ew, region_values=rv)
    for key, regions in GROUP_REGIONS.items():
        out[key]["after"] = [p for r in regions for p in after.get(r, [])]
        if out[key]["reasked"]:
            log.append(f"[{key}] after re-ask: {len(out[key]['after'])} problem(s)")
    return out


# --------------------------------------------------------------------------- #
# sceptical sanity review of the final country table
# --------------------------------------------------------------------------- #
SANITY_SYSTEM = (
    "You are a sceptical senior market analyst at Coherent Market Insights, reviewing the final country table "
    "of a market whitepaper before it goes to a client. List ONLY regions, countries or 'Rest of' groups whose "
    "size, share or rank is implausible for THIS market as defined, each with a concrete reason backed by the EVIDENCE or by "
    "well-known facts about the market (e.g. 'Turkey produces more poultry feed than the UK, yet is sized "
    "smaller'). Do not flag small differences or matters of taste. If nothing is implausible, return an empty "
    "list. Write in UK English. Return ONLY valid JSON.")
SANITY_SCHEMA = ('{"flags": [{"country": <row name exactly as in TABLE; the region name for a region row>, '
                 '"region": <its TABLE section exactly, "Global regions" for a region row>, '
                 '"problem": "too large|too small", "reason": <1-2 sentences with the evidence>}, ... 0-8 flags]}')


def table(countries: dict[str, dict[str, dict]], rest: dict[str, list[str]],
          regions: dict[str, dict] | None = None) -> dict:
    """The table the reviewer sees: the five regions (2026E value, share of
    global, CAGR) and every country (2026E value, share of region, CAGR)."""
    out = {}
    if regions:
        tot = sum(v["v26"] for v in regions.values()) or 1
        out[REGIONS_ROW] = {r: {"usd_mn_2026": round(v["v26"], 1),
                                "share_of_global_pct": round(v["v26"] / tot * 100, 1),
                                "cagr_2026_2033_pct": (round(((v["v33"] / v["v26"]) ** (1 / 7) - 1) * 100, 1)
                                                       if v["v26"] > 0 and v.get("v33") else None)}
                            for r, v in regions.items()}
    for r, d in countries.items():
        tot = sum(v["v26"] for v in d.values()) or 1
        out[r] = {k: {"usd_mn_2026": round(v["v26"], 1), "share_of_region_pct": round(v["v26"] / tot * 100, 1),
                      "cagr_2026_2033_pct": round(v.get("cagr", 0), 1),
                      **({"covers": rest.get(r, [])} if k.startswith("Rest of") else {})}
                  for k, v in sorted(d.items(), key=lambda kv: -kv[1]["v26"])}
    return out


def sanity_prompt(market: str, tbl: dict, evidence: list[str]) -> str:
    return (f"Market: {market}\n\nTABLE (2026E, USD million):\n{json.dumps(tbl, ensure_ascii=False)}\n\n"
            + ("WEB EVIDENCE:\n" + "\n".join(f"- {e}" for e in evidence) if evidence else "(no web evidence found)")
            + f"\n\nReturn ONLY valid JSON matching exactly this shape:\n{SANITY_SCHEMA}")


def resolve_flags(raw: dict | None, tbl: dict, fields: dict, geo: dict, ask: Callable[[str, str], dict | None],
                  log: list[str], *, regional: dict | None = None,
                  ask_regional: Callable[[str], dict | None] | None = None, global_value: float | None = None,
                  regional_first: dict | None = None) -> dict:
    """For each region with a flagged country, re-asks its country agent once
    (flags attached). A region's re-asked split is kept only if it passes
    check_split. Flagged region rows re-ask the regional agent once
    (`ask_regional`); its answer is kept (as "regional_answer") only if it
    passes check_regions and is not single-region balancing against the
    agent's first answer (`regional_first`). Every flag and its outcome is
    recorded."""
    flags = []
    for f in (raw or {}).get("flags") or []:
        if not isinstance(f, dict):
            continue
        r, c = f.get("region"), canonical(f.get("country") or "")
        if c in MAIN_REGIONS:
            r = REGIONS_ROW
        c = next((k for k in (tbl.get(r) or {}) if k.lower() == c.lower()), None)
        if c is None:
            flags.append({**f, "outcome": "ignored: not a row of the table", "open": False})
            continue
        row = tbl[r][c]
        flags.append({"country": c, "region": r, "problem": f.get("problem"), "reason": f.get("reason"),
                      "share_before": row.get("share_of_region_pct", row.get("share_of_global_pct"))})
    out: dict = {"flags": flags}
    mine = [f for f in flags if f.get("region") == REGIONS_ROW and "outcome" not in f]
    if mine:
        out.update(_resolve_region_flags(mine, regional, ask_regional, global_value, regional_first, log))
    ew, rv = elsewhere_values(regional)
    for key, regions in GROUP_REGIONS.items():
        mine = [f for f in flags if f.get("region") in regions and "outcome" not in f]
        if not mine or not isinstance(fields.get(key), dict) or "error" in fields[key]:
            for f in mine:
                f.update(outcome="not re-asked: no answer from the country agent", open=True)
            continue
        extra = "A sceptical review of the final country table flagged:\n" + "\n".join(
            f"- {f['country']} ({f['region']}): {f['problem']} -- {f['reason']}" for f in mine)
        log.append(f"[geo_review] {len(mine)} flag(s) on {key} -- re-asking once")
        try:
            again = ask(key, reask_note([], fields[key], extra))
        except Exception as exc:  # noqa: BLE001
            log.append(f"[geo_review] re-ask of {key} failed: {exc}")
            again = None
        new = keyed(again) if isinstance(again, dict) and "error" not in again else {}
        for r in sorted({f["region"] for f in mine}):
            cand = {**splits_of(fields), r: new.get(r) or {}}
            probs = check_splits(cand, geo, elsewhere=ew, region_values=rv).get(r) if new.get(r) else None
            accepted = probs == []
            if accepted:
                fields[key] = {**fields[key], r: new[r]}
            for f in (f for f in mine if f["region"] == r):
                after = _num((keyed(new.get(r)).get(f["country"]) or {}).get("share_pct_of_region")) if new.get(r) else None
                moved = accepted and after is not None and abs(after - f["share_before"]) >= 0.1
                f.update(share_after=after if accepted else f["share_before"],
                         outcome=("re-asked split kept" if accepted else
                                  "re-asked split rejected (" + "; ".join(p["detail"] for p in probs) + ")" if probs
                                  else "re-ask gave no split for this region"),
                         open=not moved)
    return out


def _resolve_region_flags(mine: list[dict], regional: dict | None, ask_regional, global_value,
                          regional_first: dict | None, log: list[str]) -> dict:
    """Region-row flags: one re-ask of the regional agent, kept only if coherent."""
    if ask_regional is None or not global_value:
        for f in mine:
            f.update(outcome="not re-asked: no regional agent", open=True)
        return {}
    extra = ("A sceptical review of the final table flagged these regions:\n" + "\n".join(
        f"- {f['country']}: {f['problem']} -- {f['reason']}" for f in mine)
        + f"\nThe global market is ${global_value:,.1f}M in the base year; the five regions must sum to it. "
          "Spread any correction across the regions according to the evidence, not into one region.")
    log.append(f"[geo_review] {len(mine)} region flag(s) -- re-asking the regional agent once")
    try:
        again = ask_regional(reask_note([], regional or {}, extra))
    except Exception as exc:  # noqa: BLE001
        log.append(f"[geo_review] regional re-ask failed: {exc}")
        again = None
    probs = (check_regions(again, global_value) + balancing(regional_first or regional, again)
             if _valid_regions(again) else None)
    accepted = probs == []
    new = _values(again) if accepted else {}
    tot = sum(new.values()) or 1
    for f in mine:
        after = round(new[f["country"]] / tot * 100, 1) if accepted else f["share_before"]
        f.update(share_after=after,
                 outcome=("re-asked regions kept" if accepted else
                          "re-asked regions rejected (" + "; ".join(p["detail"] for p in probs) + ")" if probs
                          else "re-ask gave no valid regions"),
                 open=not (accepted and abs(after - f["share_before"]) >= 0.1))
    if not accepted:
        return {}
    ans = {k: v for k, v in again.items() if k != "_sources"}
    return {"regional_answer": ans, "regional_changes": _region_changes(regional, ans)}


# --------------------------------------------------------------------------- #
# final verdict (computed by model.build from the final answers)
# --------------------------------------------------------------------------- #
def review(splits: dict[str, dict], geo: dict, record: dict | None, *, regional_raw: dict | None,
           global_value: float) -> dict:
    """m["geo_review"]: every check on the answers the model was built from,
    and what happened to each failure. A split that fails only on its sum is
    normalised proportionally (logged); a failure the re-ask decides (SOFT) is
    a warning; any other failure is a blocker. A failure with no re-ask on
    record (data from before PLAN-03) is a blocker too."""
    record = record or {}
    reg_rec = record.get("regional") or {}
    regional = {"regions": (reg_rec.get("answer") or {}).get("regions")} if reg_rec.get("answer") else regional_raw
    blockers, warnings, log = [], [], []

    def verdict(label: str, probs: list[dict], reasked: bool, soft: set[str] = SOFT) -> str:
        if not probs:
            return "ok"
        kinds = {p["kind"] for p in probs}
        detail = "; ".join(p["detail"] for p in probs)
        if not reasked:
            blockers.append(f"{label}: {detail} (not re-asked)")
            return "blocker"
        if kinds == {"sum"}:
            log.append(f"{label}: {detail} after one re-ask -- normalised proportionally")
            return "normalised"
        hard = [p for p in probs if p["kind"] not in soft | {"sum"}]
        if hard:
            blockers.append(f"{label}: {'; '.join(p['detail'] for p in hard)} (still failing after one re-ask)")
            return "blocker"
        warnings.append(f"{label}: {'; '.join(p['detail'] for p in probs if p['kind'] in soft)} "
                        f"(kept after one re-ask)")
        if "sum" in kinds:
            log.append(f"{label}: sum still off after one re-ask -- normalised proportionally")
        return "warning"

    reg_probs = check_regions(regional, global_value) + (balancing(regional_raw, regional)
                                                         if reg_rec.get("answer") else [])
    total = sum(_num((d or {}).get("value_usd_mn")) or 0 for d in ((regional or {}).get("regions") or {}).values())
    regional_out = {"sum_usd_mn": round(total, 1), "global_usd_mn": round(global_value, 1),
                    "ratio": round(total / global_value, 3) if global_value else None,
                    "source": "re-asked regional answer" if reg_rec.get("answer") else "regional agent (fields_raw)",
                    "problems": reg_probs, "first_problems": reg_rec.get("problems"),
                    "reasked": bool(reg_rec.get("reasked")), "changes": reg_rec.get("changes") or []}
    regional_out["balancing_first"] = reg_rec.get("balancing") or []
    regional_out["sanity_changes"] = reg_rec.get("sanity_changes") or []
    regional_out["status"] = verdict("Regions", reg_probs, bool(reg_rec.get("reasked")), soft=set())

    ew, rv = elsewhere_values(regional)
    probs = check_splits(splits, geo, elsewhere=ew, region_values=rv)
    srec = record.get("splits") or {}
    checks = {}
    for r, ps in probs.items():
        rec = srec.get(REGION_GROUP.get(r, "")) or {}
        checks[r] = {"rest_share_pct": rest_share(r, splits.get(r) or {}),
                     "sum_pct": round(sum(_num((v or {}).get("share_pct_of_region")) or 0
                                          for v in keyed(splits.get(r)).values() if isinstance(v, dict)), 2),
                     "problems": ps, "first_problems": [p for p in rec.get("problems") or [] if p["region"] == r],
                     "reasked": bool(rec.get("reasked")),
                     "changes": [c for c in rec.get("changes") or [] if c.startswith(r + "/")]}
        checks[r]["status"] = verdict(r, ps, bool(rec.get("reasked")))

    flags = (record.get("sanity") or {}).get("flags")
    for f in flags or []:
        if f.get("open"):
            warnings.append(f"sanity review: {f.get('country')} ({f.get('region')}) {f.get('problem')} -- "
                            f"{f.get('reason')} [{f.get('outcome')}]")
    return {"regional": regional_out, "splits": checks,
            "sanity": {"ran": flags is not None, "flags": flags or []},
            "geography_reask": record.get("geography"),
            "normalised": log, "blockers": blockers, "warnings": warnings}
