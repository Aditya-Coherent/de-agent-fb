"""Checks the desk-research agents' segments against the market definition,
and re-asks the same agent once when an answer is incoherent (PLAN-04, same
pattern as geo_check.py). Nothing here produces a number: a segment mix is
the agent's own answer, or its second answer after it was shown what was
wrong with the first. What the checks may do to an answer is limited to
dropping a segment the definition excludes, merging a pair that still
overlaps after the re-ask, and switching off a type dimension that is a
false split -- each one logged.

- check_global / review_global: segments_global's two dimensions -- segments
  the definition excludes (or at another value-chain level), segments without
  a definition, overlapping pairs, and false type splits; one re-ask.
- resolve / combine / merge_raw: what model.build does with the final answer.
- check_mix / review_mea: the MEA aggregate of segments_mea's country mixes
  against the global mix (x2 either way); one re-ask with the global mix shown.
  No blending: still beyond x2 after the re-ask is a warning.
- check_use_cases / review_use_cases: heat-map rows against the definition;
  one re-ask, then rows still out of scope are left out.
- review: the verdict recorded in the model as m["segment_review"] (blockers,
  warnings, changes; PLAN-08 turns blockers into release-gate failures).
"""
from __future__ import annotations

import json
import re
from itertools import combinations
from typing import Callable

from AiAgentsFleet.cmi_report.guard import NEGATED, excluded_hit, market_stems, scope_stems, scope_violation

OVERLAP_MAX = 0.60          # two segment definitions sharing more content words than this overlap
MIX_FACTOR = 2.0            # MEA share vs global share, either way
MIX_MIN_GAP_PP = 2.0        # ...and at least this many points apart (tiny segments swing by ratio alone)
REPLACE_MIN, REPLACE_MAX = 4, 7   # a replacement type taxonomy: 4-7 types...
REPLACE_SUM = (98.0, 102.0)        # ...whose shares sum to 100 (+/-2)
COPY_TOL = 0.10             # every country within +/-10% (relative) of the global mix on every segment = a copy
# the "false split" test (do buyers price/specify the types differently) applies to these levels only; a
# farm-gate output, producer or service market may split by production system, technology or species
FALSE_SPLIT_LEVELS = {"manufacturer", "vendor/software", "retail"}
DIMS = ("type", "demand")


def _num(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _problem(dim: str, kind: str, segments: list[str], detail: str) -> dict:
    return {"dim": dim, "kind": kind, "segments": segments, "detail": detail}


def _is_other(name: str) -> bool:
    return str(name).strip().lower().startswith("other")


def defs(sg: dict | None, dim: str) -> dict[str, str]:
    """{lower-case segment name: its one-line definition} for one dimension."""
    return _defs(sg or {}, dim)


def _defs(sg: dict, dim: str) -> dict[str, str]:
    d = ((sg or {}).get("segment_definitions") or {}).get(dim) or {}
    out = {str(k).lower(): str(v or "") for k, v in d.items()} if isinstance(d, dict) else {}
    for m in merges(sg):                    # a merged type is defined by its merge entry
        if dim == "type" and m.get("definition"):
            out[m["name"].lower()] = str(m["definition"])
    return out


def _head(text: str) -> str:
    """A definition's first clause -- what the segment IS, not what its buyers also do."""
    return re.split(r"[,;:]|\s(?:that|which|who|including|such as|e\.g\.)\s", text or "", maxsplit=1)[0]


# --------------------------------------------------------------------------- #
# segments_global
# --------------------------------------------------------------------------- #
def merges(sg: dict | None) -> list[dict]:
    """The agent's type merges: [{"merge": [a, b, ...], "name", "definition"}]."""
    out = []
    for m in (sg or {}).get("type_merges") or []:
        names = [str(x) for x in (m.get("merge") or []) if str(x).strip()] if isinstance(m, dict) else []
        if len(names) >= 2:
            out.append({"merge": names, "name": str(m.get("name") or " / ".join(names)).strip(),
                        "definition": str(m.get("definition") or "")})
    return out


def bad_merges(sg: dict | None, type_names: list[str]) -> list[str]:
    """Source types the agent put into two different merged segments at once
    (e.g. 'Seaweed Extracts' in both 'Extract-Based (incl. Seaweed)' and
    'Other (incl. Seaweed)') -- such merges are rejected as a whole."""
    low = {n.lower(): n for n in type_names}
    seen: dict[str, str] = {}
    out = []
    for m in merges(sg):
        for x in m["merge"]:
            src = low.get(x.lower())
            if src and src in seen and seen[src] != m["name"] and src not in out:
                out.append(src)
            elif src:
                seen[src] = m["name"]
    return out


def merge_map(sg: dict | None, type_names: list[str]) -> dict[str, str]:
    """{original type name: merged name} for the agent's own merges of real
    types; {} when they put one type into two merges (bad_merges)."""
    low = {n.lower(): n for n in type_names}
    out = {}
    if bad_merges(sg, type_names):
        return out
    for m in merges(sg):
        members = [low[x.lower()] for x in m["merge"] if x.lower() in low]
        if len(members) >= 2:
            out.update({x: m["name"] for x in members})
    return out


def replacement(sg: dict | None) -> list[dict] | None:
    """The agent's replacement type taxonomy ([{"name", "share_pct_2026",
    "cagr_2026_2033_pct", "definition"}]), or None unless it is well formed:
    REPLACE_MIN-REPLACE_MAX distinct named types, each with a share and a
    definition, shares summing to 100 (+/-2)."""
    rows = (sg or {}).get("type_replacement")
    if not isinstance(rows, list) or not REPLACE_MIN <= len(rows) <= REPLACE_MAX:
        return None
    out = []
    for r in rows:
        if not isinstance(r, dict):
            return None
        name, share = str(r.get("name") or "").strip(), _num(r.get("share_pct_2026"))
        if not name or share is None or share < 0 or not str(r.get("definition") or "").strip():
            return None
        out.append({"name": name, "share_pct_2026": share, "cagr_2026_2033_pct": _num(r.get("cagr_2026_2033_pct")),
                    "definition": str(r["definition"]).strip()})
    if len({r["name"].lower() for r in out}) != len(out) or \
            not REPLACE_SUM[0] <= sum(r["share_pct_2026"] for r in out) <= REPLACE_SUM[1]:
        return None
    return out


def with_replacement(sg: dict) -> dict:
    """segments_global with its replacement taxonomy adopted as THE type list:
    names, CAGRs and definitions from the replacement, no type merges."""
    rep_ = replacement(sg) or []
    defs_ = dict((sg.get("segment_definitions") or {}))
    defs_["type"] = {r["name"]: r["definition"] for r in rep_}
    names = {r["name"].lower() for r in rep_}
    return {**sg, "type_taxonomy": "replacement", "type_merges": [],
            "type_cagr_2026_2033_pct": {r["name"]: r["cagr_2026_2033_pct"] for r in rep_
                                        if r["cagr_2026_2033_pct"] is not None},
            "segment_definitions": defs_,
            "type_split_check": [x for x in sg.get("type_split_check") or [] if isinstance(x, dict)
                                 and str(x.get("a", "")).lower() in names and str(x.get("b", "")).lower() in names]}


def types_of(fleet_types: list[dict], sg: dict | None) -> list[dict]:
    """The type list the report uses: the accepted replacement taxonomy, or
    the fleet's own types ([{"name", "share_pct_of_market"}])."""
    rep_ = replacement(sg) if (sg or {}).get("type_taxonomy") == "replacement" else None
    if rep_:
        return [{"name": r["name"], "share_pct_of_market": r["share_pct_2026"]} for r in rep_]
    return fleet_types


def type_names_of(sg: dict | None, fleet_names: list[str]) -> list[str]:
    return [t["name"] for t in types_of([{"name": n} for n in fleet_names], sg)]


def names_of(sg: dict | None, type_names: list[str]) -> dict[str, list[str]]:
    """{dim: segment names} after the agent's own type merges."""
    mm = merge_map(sg, type_names)
    types = list(dict.fromkeys(mm.get(n, n) for n in type_names))
    dem = [str(d.get("name")) for d in (sg or {}).get("demand_segments") or [] if isinstance(d, dict) and d.get("name")]
    return {"type": types, "demand": dem}


def false_split_applies(definition: dict | None) -> bool:
    return str((definition or {}).get("value_chain_level") or "").lower() in FALSE_SPLIT_LEVELS


def false_splits(sg: dict | None, type_names: list[str],
                 definition: dict | None = None) -> list[tuple[str, str, str]]:
    """Type pairs the agent says buyers do not price or specify differently
    (only production history differs), and that it has not merged. Only for
    manufacturer, vendor/software and retail markets (FALSE_SPLIT_LEVELS)."""
    if not false_split_applies(definition):
        return []
    mm = merge_map(sg, type_names)
    low = {n.lower(): n for n in type_names}
    out = []
    for r in (sg or {}).get("type_split_check") or []:
        if not isinstance(r, dict):
            continue
        a, b = low.get(str(r.get("a", "")).lower()), low.get(str(r.get("b", "")).lower())
        basis = str(r.get("basis") or "").lower()
        false = r.get("buyers_differentiate") is False or any(w in basis for w in ("history", "route"))
        if a and b and a != b and false and not (mm.get(a) and mm.get(a) == mm.get(b)):
            out.append((a, b, basis or "buyers do not price or specify them differently"))
    return out


def overlaps(names: list[str], defs: dict[str, str], definition: dict | None,
             rules: list[str] | None = None) -> list[tuple[str, str, float]]:
    """Pairs of segments whose definitions share > OVERLAP_MAX of their content
    words (Jaccard), or whose names are one inside the other ('Extracts' within
    'Seaweed Extracts') with no allocation rule naming both. 'Other ...'
    segments are a remainder, not a peer."""
    own = market_stems(definition)
    rule_words = [scope_stems(r, own) for r in rules or [] if isinstance(r, str)]
    out = []
    for a, b in combinations([n for n in names if not _is_other(n)], 2):
        na, nb = scope_stems(a.split(" (")[0], own), scope_stems(b.split(" (")[0], own)
        da, db = scope_stems(defs.get(a.lower(), ""), own), scope_stems(defs.get(b.lower(), ""), own)
        jac = len(da & db) / len(da | db) if da and db else 0.0
        fa, fb = scope_stems(a, own), scope_stems(b, own)
        ruled = any(fa <= w and fb <= w for w in rule_words)
        if (na and nb and (na <= nb or nb <= na) and not ruled) or jac > OVERLAP_MAX:
            out.append((a, b, round(jac, 2)))
    return out


def out_of_scope(names: list[str], defs: dict[str, str], definition: dict | None) -> list[tuple[str, str]]:
    """(segment, why) for each segment the definition excludes, or at another value-chain level."""
    out = []
    for n in names:
        # The name is checked as a row. The definition's first clause is checked only for excluded
        # items of two or more words, and not when it says 'without ...' -- a farming system's
        # definition may mention the technology it uses, a farm's the processor it sells to.
        head = _head(defs.get(n.lower(), ""))
        hit = None if NEGATED.search(head) else excluded_hit(head, definition, min_words=2)
        why = scope_violation(n, definition, row=True) or (f"is defined as '{hit}', which the definition excludes"
                                                           if hit else None)
        if why:
            out.append((n, why))
    return out


def _stem_of(word: str, ignore: set[str]) -> str | None:
    st = scope_stems(word, ignore)
    return next(iter(st)) if st else None


def not_included(names: list[str], definition: dict | None) -> list[str]:
    """Segments the definition's included list does not cover, in a dimension
    the list clearly enumerates (it covers at least half of the dimension's
    segments by their leading word or half their words) -- e.g. 'Animal Feed' among applications
    when only supplements, food, pharma and cosmetics are included. Re-asked once, then a warning:
    a word heuristic does not drop a segment (only an explicit exclusion does). A dimension the list does not
    enumerate (buyer types of a farm-gate market) is not judged."""
    inc = [x for x in (definition or {}).get("included_items") or [] if isinstance(x, str)]
    if not inc:
        return []
    own = market_stems(definition)
    words = set().union(*(scope_stems(x, own) for x in inc))
    real = [n for n in names if not _is_other(n)]

    def lead(n: str) -> str | None:
        """The segment's leading content word ('Animal' of 'Animal Feed & Pet Food')."""
        return next((w for w in (_stem_of(x, own) for x in re.findall(r"[A-Za-z]+", n)) if w), None)
    def covers(n: str) -> bool:
        st = scope_stems(n, own)
        return lead(n) in words or (bool(st) and len(st & words) * 2 >= len(st))
    covered = [n for n in real if covers(n)]
    if len(real) < 2 or len(covered) * 2 < len(real):
        return []
    return [n for n in real if n not in covered]


def check_global(sg: dict | None, type_names: list[str], definition: dict | None) -> list[dict]:
    """Problems with segments_global's answer ([] = coherent)."""
    if not isinstance(sg, dict) or "error" in sg:
        return []
    type_names = type_names_of(sg, type_names)
    names = names_of(sg, type_names)
    out: list[dict] = []
    bad = bad_merges(sg, type_names)
    if bad:
        out.append(_problem("type", "bad_merge", bad, f"{', '.join(repr(b) for b in bad)} put into two merged "
                                                      f"segments at once -- the merges are rejected"))
    for dim in DIMS:
        defs = _defs(sg, dim)
        for n, why in out_of_scope(names[dim], defs, definition):
            out.append(_problem(dim, "excluded", [n], f"'{n}' {why}"))
        for n in not_included(names[dim], definition):
            out.append(_problem(dim, "not_included", [n], f"'{n}' is not in the definition's included list"))
        missing = [n for n in names[dim] if not defs.get(n.lower(), "").strip()]
        if missing:
            out.append(_problem(dim, "no_definition", missing, f"no one-line definition for: {', '.join(missing)}"))
        for a, b, jac in overlaps(names[dim], defs, definition, (sg or {}).get("allocation_rules")):
            why = (f"{jac:.0%} of their definitions' words shared" if jac > OVERLAP_MAX
                   else "one name contains the other, and no allocation rule separates them")
            out.append(_problem(dim, "overlap", [a, b], f"'{a}' and '{b}' overlap ({why})"))
    for a, b, basis in false_splits(sg, type_names, definition):
        out.append(_problem("type", "false_split", [a, b], f"'{a}' vs '{b}' is a false split ({basis}): buyers do "
                                                          f"not price or specify them differently"))
    return out


# type problems answered with a replacement taxonomy: overlapping types, a false split (two types that are
# really one product) and rejected merges -- asking for merges on these put one type into two merges
REPLACE_KINDS = {"overlap", "bad_merge", "false_split"}


def wants_replacement(problems: list[dict]) -> bool:
    return any(p["dim"] == "type" and p["kind"] in REPLACE_KINDS for p in problems)


def reask_note_global(problems: list[dict], previous: dict, definition: dict | None = None) -> str:
    """Appended to segments_global's original prompt for its one re-ask.
    Overlapping types are answered with a REPLACEMENT taxonomy, not merges
    (merging an overlapping starting list tends to put one type in two merges)."""
    prev = {k: v for k, v in (previous or {}).items() if k != "_sources"}
    why = ("they overlap, or differ only by production history" if false_split_applies(definition)
           else "they genuinely overlap; distinct production systems, technologies or species stay separate")
    if wants_replacement(problems):
        types_rule = (
            f"- the type segments overlap or are falsely split, so do NOT merge them: return a REPLACEMENT taxonomy in "
            f"\"type_replacement\": a mutually exclusive, collectively exhaustive list of {REPLACE_MIN}-{REPLACE_MAX} "
            "types, using the standard segmentation that published reports on this market use, with a one-line "
            "definition each and shares summing to 100: [{\"name\": <string>, \"share_pct_2026\": <number>, "
            "\"cagr_2026_2033_pct\": <number>, \"definition\": <one line>}, ...]. No name may contain another "
            "type's name. Keep the same total market; set \"type_merges\": [].\n")
    else:
        types_rule = (f"- the type names are fixed by CONTEXT.types: if two types are really one ({why}), list them "
                      "in \"type_merges\": [{\"merge\": [<type>, <type>], \"name\": <merged name>, "
                      "\"definition\": <one line>}]; a type may appear in ONE merge only.\n")
    return ("\n\nYOUR PREVIOUS ANSWER (below) failed these checks against the DEFINITION:\n"
            + "\n".join(f"- {p['dim']}: {p['detail']}" for p in problems)
            + "\nFix them and return a corrected answer in exactly the same shape:\n"
              "- remove every segment the DEFINITION excludes, does not include, or that sits at another "
              "value-chain level, and "
              "replace it with an in-scope one where the dimension needs it;\n"
              "- give every segment a one-line definition, and make the segments of each dimension mutually "
              "exclusive (rewrite overlapping definitions, or use an allocation rule);\n"
            + types_rule
            + f"PREVIOUS ANSWER:\n{json.dumps(prev, ensure_ascii=False)}")


_TYPE_BLOCKING = {"overlap", "bad_merge", "false_split", "excluded"}


def _valid_global(ans) -> bool:
    return isinstance(ans, dict) and "error" not in ans and isinstance(ans.get("demand_segments"), list) \
        and len(ans["demand_segments"]) >= 2


def _seg_changes(before: dict, after: dict, type_names: list[str]) -> list[str]:
    b = names_of(before, type_names_of(before, type_names))
    a = names_of(after, type_names_of(after, type_names))
    out = []
    for dim in DIMS:
        if b[dim] != a[dim]:
            out.append(f"{dim}: {b[dim]} -> {a[dim]}")
    if merges(after) != merges(before):
        out.append("type merges: " + "; ".join(f"{' + '.join(m['merge'])} -> {m['name']}" for m in merges(after)))
    return out


def review_global(fields: dict, type_names: list[str], definition: dict | None,
                  ask: Callable[[str, str], dict | None], log: list[str]) -> dict:
    """Checks segments_global and re-asks it once when it fails, keeping the
    second answer in `fields`. Returns the record kept in cmi_fields
    segment_review["segments_global"]."""
    sg = fields.get("segments_global")
    probs = check_global(sg, type_names, definition)
    rec: dict = {"problems": probs, "reasked": False}
    if not probs:
        rec["after"] = []
        return rec
    log.append("[segments_global] " + "; ".join(f"{p['dim']}: {p['detail']}" for p in probs)[:500]
               + " -- re-asking once")
    rec["reasked"] = True
    try:
        again = ask("segments_global", reask_note_global(probs, sg, definition))
    except Exception as exc:  # noqa: BLE001
        log.append(f"[segments_global] re-ask failed: {exc}")
        again = None
    if _valid_global(again):
        if wants_replacement(probs):
            rows = replacement(again)
            if rows is None:
                rec["replacement"] = "missing or malformed -- merges fall back"
            else:
                cand = with_replacement(again)
                bad = [p for p in check_global(cand, type_names, definition)
                       if p["dim"] == "type" and p["kind"] in _TYPE_BLOCKING]
                if bad:
                    rec["replacement"] = "rejected (still overlapping) -- merges fall back"
                    rec["replacement_problems"] = bad
                    again = {k: v for k, v in again.items() if k != "type_replacement"}
                else:
                    rec["replacement"] = "accepted"
                    rec["types"] = [{"name": r["name"], "share_pct_2026": r["share_pct_2026"]} for r in rows]
                    again = cand
            log.append(f"[segments_global] replacement type taxonomy: {rec['replacement']}")
        rec["changes"] = _seg_changes(sg, again, type_names)
        fields["segments_global"] = again
    else:
        rec["reask_failed"] = True
    rec["after"] = check_global(fields.get("segments_global"), type_names, definition)
    log.append(f"[segments_global] after re-ask: {len(rec['after'])} problem(s)")
    return rec


def resolve(sg: dict | None, type_names: list[str], definition: dict | None) -> dict:
    """What the model does with segments_global's final answer:
    {"names": {dim: [kept names]}, "merge": {dim: {member: merged}}, "dropped":
    {dim: [(name, why)]}, "type_off": reason | None, "notes": [...], "all_excluded": [dim]}.
    Excluded segments are dropped (never every one of a dimension); pairs still
    overlapping are merged; a remaining false type split switches the type
    dimension off."""
    type_names = type_names_of(sg, type_names)
    names = names_of(sg, type_names)
    merge = {"type": merge_map(sg, type_names), "demand": {}}
    notes = [f"type: '{a}' merged into '{b}' (agent's merge)" for a, b in merge["type"].items() if a != b]
    dropped: dict[str, list[tuple[str, str]]] = {d: [] for d in DIMS}
    all_excluded = []
    for dim in DIMS:
        defs = _defs(sg, dim)
        bad = out_of_scope(names[dim], defs, definition)
        if bad and len(bad) == len(names[dim]):
            all_excluded.append(dim)
        elif bad:
            dropped[dim] = bad
            names[dim] = [n for n in names[dim] if n not in {b for b, _ in bad}]
            notes += [f"{dim}: dropped '{n}' ({why}); remaining shares renormalised to 100%" for n, why in bad]
        for a, b, _ in overlaps(names[dim], defs, definition, (sg or {}).get("allocation_rules")):
            if a in names[dim] and b in names[dim]:
                into = f"{a} / {b}"
                for x in (a, b):
                    for k, v in list(merge[dim].items()):
                        if v == x:
                            merge[dim][k] = into
                    merge[dim][x] = into
                names[dim] = [into if n == a else n for n in names[dim] if n != b]
                notes.append(f"{dim}: '{a}' and '{b}' overlap -- merged as '{into}'")
    fs = [(a, b) for a, b, _ in false_splits(sg, type_names, definition) if (merge["type"].get(a, a) in names["type"]
                                                                 and merge["type"].get(b, b) in names["type"]
                                                                 and merge["type"].get(a, a) != merge["type"].get(b, b))]
    type_off = (f"false split: {'; '.join(f'{a} vs {b}' for a, b in fs)} differ only in production history"
                if fs else None)
    if type_off:
        notes.append(f"type dimension not shown ({type_off})")
    return {"names": names, "merge": merge, "dropped": dropped, "type_off": type_off, "notes": notes,
            "all_excluded": all_excluded}


def combine(parts: dict[str, tuple[float, float]], merge: dict[str, str],
            keep: list[str]) -> dict[str, tuple[float, float]]:
    """{name: (share, cagr)} with merged members summed (CAGR share-weighted)
    and only `keep` left; allocate() renormalises the shares."""
    acc: dict[str, list[float]] = {}
    for n, (sh, g) in parts.items():
        k = merge.get(n, n)
        a = acc.setdefault(k, [0.0, 0.0])
        a[0] += max(float(sh), 0)
        a[1] += max(float(sh), 0) * float(g)
    out = {}
    for n in keep:
        if n in acc:
            sh, w = acc[n]
            out[n] = (sh, w / sh if sh else next((g for m, (s, g) in parts.items() if merge.get(m, m) == n), 0.0))
    return out


def merge_raw(raw: dict | None, merge: dict[str, str]) -> dict:
    """A country's {name: {"share_pct", "cagr_pct"}} mix with merged members
    summed under the merged name (unless the agent already used that name)."""
    raw = {str(k): v for k, v in (raw or {}).items()} if isinstance(raw, dict) else {}
    low = {k.lower(): k for k in raw}
    out = dict(raw)
    for target in set(merge.values()):
        if target.lower() in low:
            continue
        members = [low[m.lower()] for m, t in merge.items() if t == target and m.lower() in low]
        vals = [raw[m] for m in members if isinstance(raw[m], dict)]
        if not vals:
            continue
        sh = sum(_num(v.get("share_pct")) or 0 for v in vals)
        g = (sum((_num(v.get("share_pct")) or 0) * (_num(v.get("cagr_pct")) or 0) for v in vals) / sh) if sh else \
            (_num(vals[0].get("cagr_pct")) or 0)
        out[target] = {"share_pct": sh, "cagr_pct": g}
    return out


def global_mix(types: list[dict], sg: dict | None, resolved: dict) -> dict[str, dict[str, float]]:
    """{dim: {segment: % of the 2026 global market}} for the segments the model keeps."""
    parts = {"type": {t["name"]: (float(t.get("share_pct_of_market") or 0), 0.0) for t in types_of(types, sg)},
             "demand": {str(d["name"]): (float(d.get("share_pct_2026") or 0), 0.0)
                        for d in (sg or {}).get("demand_segments") or [] if isinstance(d, dict) and d.get("name")}}
    out = {}
    for dim in DIMS:
        kept = combine(parts[dim], resolved["merge"][dim], resolved["names"][dim])
        tot = sum(s for s, _ in kept.values()) or 1
        out[dim] = {n: s * 100 / tot for n, (s, _) in kept.items()}
    return out


# --------------------------------------------------------------------------- #
# segments_mea: the MEA aggregate against the global mix
# --------------------------------------------------------------------------- #
def mea_shares(unit_values: dict[str, float], smea: dict, dim: str, names: list[str],
               merge: dict[str, str] | None = None) -> dict[str, float]:
    """{segment: % of MEA} -- each country's mix weighted by the country's value."""
    from AiAgentsFleet.cmi_report.geo_check import keyed
    sm = keyed(smea)
    acc = {n: 0.0 for n in names}
    for unit, v in unit_values.items():
        mix = merge_raw((sm.get(unit) or {}).get(dim), merge or {})
        low = {k.lower(): x for k, x in mix.items() if isinstance(x, dict)}
        shares = {n: max(_num((low.get(n.lower()) or {}).get("share_pct")) or 0, 0) for n in names}
        tot = sum(shares.values())
        if tot <= 0:
            continue
        for n in names:
            acc[n] += v * shares[n] / tot
    total = sum(acc.values())
    return {n: acc[n] * 100 / total for n in names} if total else {}


def mea_names_differ(smea: dict | None, type_names: list[str], merge: dict[str, str] | None = None) -> bool:
    """True when segments_mea has country mixes but not one of them names
    every global type (e.g. it was answered before a replacement taxonomy):
    the missing types would silently take their global share everywhere."""
    from AiAgentsFleet.cmi_report.geo_check import keyed
    if not isinstance(smea, dict) or "error" in smea or not type_names:
        return False
    want = {n.lower() for n in type_names}
    mixes = [merge_raw(v.get("type"), merge or {}) for k, v in keyed(smea).items()
             if isinstance(v, dict) and isinstance(v.get("type"), dict)]
    return bool(mixes) and not any(want <= {str(k).lower() for k in m} for m in mixes)


def check_mix(global_shares: dict[str, float], mea: dict[str, float], dim: str,
              reasons: dict | None = None) -> list[dict]:
    """A segment whose MEA share is more than MIX_FACTOR times (or less than
    1/MIX_FACTOR of) its global share, and MIX_MIN_GAP_PP points away."""
    out = []
    reasons = {str(k).lower(): v for k, v in (reasons or {}).items()} if isinstance(reasons, dict) else {}
    for n, g in global_shares.items():
        m = mea.get(n)
        if m is None or g is None:
            continue
        ratio = (m / g) if g > 0 else float("inf")
        if (ratio > MIX_FACTOR or ratio < 1 / MIX_FACTOR) and abs(m - g) >= MIX_MIN_GAP_PP:
            p = _problem(dim, "mix", [n], f"{n}: {m:.1f}% of MEA vs {g:.1f}% globally (x{ratio:.2f})")
            p.update({"global_pct": round(g, 2), "mea_pct": round(m, 2), "ratio": round(ratio, 2)})
            if reasons.get(n.lower()):
                p["mea_reason"] = str(reasons[n.lower()])
            out.append(p)
    return out


def unit_mix(smea: dict | None, unit: str, dim: str, names: list[str],
             merge: dict[str, str] | None = None) -> dict[str, float] | None:
    """One country's mix as {segment: % of that country}, or None if it gave none."""
    from AiAgentsFleet.cmi_report.geo_check import keyed
    raw = merge_raw((keyed(smea).get(unit) or {}).get(dim), merge or {})
    low = {k.lower(): x for k, x in raw.items() if isinstance(x, dict)}
    shares = {n: max(_num((low.get(n.lower()) or {}).get("share_pct")) or 0, 0) for n in names}
    tot = sum(shares.values())
    return {n: v * 100 / tot for n, v in shares.items()} if tot > 0 else None


def copies_global(unit_mixes: dict[str, dict[str, dict[str, float] | None]],
                  global_mix: dict[str, dict[str, float]]) -> bool:
    """True when EVERY country's type and demand mix is within COPY_TOL
    (relative) of the global mix on every segment: the global mix was
    copied, not estimated per country."""
    if not unit_mixes:
        return False
    for mixes in unit_mixes.values():
        for dim, g in global_mix.items():
            m = (mixes or {}).get(dim)
            if not m:
                return False
            if any(gv > 0 and abs(m.get(n, 0) - gv) / gv > COPY_TOL for n, gv in g.items()):
                return False
    return True


def _unit_mixes(smea, units, global_mix, merge) -> dict:
    return {u: {dim: unit_mix(smea, u, dim, list(g), (merge or {}).get(dim)) for dim, g in global_mix.items()}
            for u in units}


def reask_note_copied(global_mix: dict[str, dict[str, float]], previous: dict) -> str:
    """The re-ask for MEA mixes that copy the global mix."""
    prev = {k: v for k, v in (previous or {}).items() if k != "_sources"}
    return ("\n\nYOUR PREVIOUS ANSWER (below) gives every MEA country practically the same mix as the GLOBAL "
            "market (within 10% on every segment).\nGLOBAL MIX (% of the 2026 global market): "
            + json.dumps({d: {n: round(v, 1) for n, v in m.items()} for d, m in global_mix.items()}, ensure_ascii=False)
            + "\nThat is a copy, not an estimate. Re-examine the EVIDENCE and give each country its own mix, based on "
              "what it actually grows or makes (crops, industry structure) and its policy priorities; keep a share "
              "close to the global one only where the evidence supports it. Return exactly the same shape.\n"
            + f"PREVIOUS ANSWER:\n{json.dumps(prev, ensure_ascii=False)}")


def reask_note_mea(problems: list[dict], global_mix: dict[str, dict[str, float]], previous: dict) -> str:
    prev = {k: v for k, v in (previous or {}).items() if k != "_sources"}
    return ("\n\nYOUR PREVIOUS ANSWER (below) gives an MEA segment mix that contradicts the GLOBAL mix "
            "(weighted by each country's size in the report):\n"
            + "\n".join(f"- {p['dim']}: {p['detail']}" for p in problems)
            + "\nGLOBAL MIX (% of the 2026 global market): "
            + json.dumps({d: {n: round(v, 1) for n, v in m.items()} for d, m in global_mix.items()}, ensure_ascii=False)
            + "\nRe-examine each country's mix against the evidence and return a corrected answer in exactly the same "
              "shape. Countries may differ from the global mix, but a segment that is small globally cannot be the "
              "largest across most MEA countries without a concrete reason. If the evidence shows MEA really differs, "
              "keep it and add \"mea_reason\": {\"<segment>\": <1 sentence with the evidence>}.\n"
            + f"PREVIOUS ANSWER:\n{json.dumps(prev, ensure_ascii=False)}")


def review_mea(fields: dict, unit_values: dict[str, float], global_mix: dict[str, dict[str, float]],
               ask: Callable[[str, str], dict | None], log: list[str], merge: dict[str, dict] | None = None) -> dict:
    """Checks segments_mea's MEA aggregate against the global mix and re-asks
    it once when a segment is beyond x2, keeping the second answer in `fields`.
    Returns the record kept in cmi_fields segment_review["segments_mea"]."""
    merge = merge or {}

    def problems(ans):
        out = []
        for dim, g in global_mix.items():
            out += check_mix(g, mea_shares(unit_values, ans, dim, list(g), merge.get(dim)), dim,
                             (ans or {}).get("mea_reason"))
        return out
    smea = fields.get("segments_mea")
    if not isinstance(smea, dict) or "error" in smea:
        return {"problems": [], "reasked": False, "skipped": "no segments_mea answer"}
    probs = problems(smea)
    rec: dict = {"problems": probs, "reasked": False}
    if not probs:
        rec["after"] = []
        # no x2 conflict -- but mixes that just copy the global one are not estimates either
        rec["copied_first"] = copies_global(_unit_mixes(smea, unit_values, global_mix, merge), global_mix)
        if rec["copied_first"]:
            log.append("[segments_mea] MEA mixes copy the global mix -- re-asking once for country-specific mixes")
            rec["reasked"] = True
            try:
                again = ask("segments_mea", reask_note_copied(global_mix, smea))
            except Exception as exc:  # noqa: BLE001
                log.append(f"[segments_mea] re-ask failed: {exc}")
                again = None
            from AiAgentsFleet.cmi_report.geo_check import keyed
            if not (isinstance(again, dict) and "error" not in again and set(keyed(again)) >= set(unit_values)):
                rec["reask_failed"] = True
            elif problems(again):            # kept only if it passes the x2 check
                rec["copied_reask_rejected"] = [p["detail"] for p in problems(again)]
                log.append("[segments_mea] re-asked mixes fail the x2 check -- first answer kept: "
                           + "; ".join(rec["copied_reask_rejected"])[:300])
            else:
                fields["segments_mea"] = again
                rec["changes"] = ["country-specific mixes from the re-ask kept"]
            rec["copied_after"] = copies_global(_unit_mixes(fields["segments_mea"], unit_values, global_mix, merge),
                                                global_mix)
            log.append(f"[segments_mea] after re-ask: the mixes {'still copy' if rec['copied_after'] else 'no longer copy'}"
                       f" the global mix")
        return rec
    log.append("[segments_mea] " + "; ".join(f"{p['dim']}: {p['detail']}" for p in probs)[:500] + " -- re-asking once")
    rec["reasked"] = True
    try:
        again = ask("segments_mea", reask_note_mea(probs, global_mix, smea))
    except Exception as exc:  # noqa: BLE001
        log.append(f"[segments_mea] re-ask failed: {exc}")
        again = None
    from AiAgentsFleet.cmi_report.geo_check import keyed
    if isinstance(again, dict) and "error" not in again and set(keyed(again)) >= set(unit_values):
        rec["changes"] = [f"{p['dim']}/{p['segments'][0]}: {p['mea_pct']}% -> "
                          f"{round(mea_shares(unit_values, again, p['dim'], list(global_mix[p['dim']]), merge.get(p['dim'])).get(p['segments'][0], 0), 2)}%"
                          for p in probs]
        fields["segments_mea"] = again
    else:
        rec["reask_failed"] = True
    rec["after"] = problems(fields["segments_mea"])
    rec["copied_after"] = copies_global(_unit_mixes(fields["segments_mea"], unit_values, global_mix, merge), global_mix)
    log.append(f"[segments_mea] after re-ask: {len(rec['after'])} segment(s) still beyond x{MIX_FACTOR:g}")
    return rec


# --------------------------------------------------------------------------- #
# use cases (heat maps)
# --------------------------------------------------------------------------- #
UC_REGIONS = ("middle_east", "africa")


def check_use_cases(uc: dict | None, definition: dict | None) -> list[dict]:
    out = []
    for region in UC_REGIONS:
        for r in (uc or {}).get(region) or []:
            name = str((r or {}).get("use_case") or "") if isinstance(r, dict) else ""
            why = scope_violation(name, definition, row=True) if name else None
            if why:
                out.append(_problem(region, "use_case", [name], f"'{name}' {why}"))
    return out


def review_use_cases(fields: dict, definition: dict | None, ask: Callable[[str, str], dict | None],
                     log: list[str]) -> dict:
    """Checks the heat-map rows and re-asks use_cases once when a row is out of
    scope; rows still out of scope after that are left out of `fields`
    (logged). Returns the record kept in segment_review["use_cases"]."""
    uc = fields.get("use_cases")
    if not isinstance(uc, dict) or "error" in uc:
        return {"problems": [], "reasked": False, "skipped": "no use_cases answer"}
    probs = check_use_cases(uc, definition)
    rec: dict = {"problems": probs, "reasked": False, "removed": []}
    if not probs:
        rec["after"] = []
        return rec
    log.append("[use_cases] " + "; ".join(p["detail"] for p in probs)[:400] + " -- re-asking once")
    rec["reasked"] = True
    prev = {k: v for k, v in uc.items() if k != "_sources"}
    note = ("\n\nYOUR PREVIOUS ANSWER (below) has use cases outside the DEFINITION:\n"
            + "\n".join(f"- {p['dim']}: {p['detail']}" for p in probs)
            + "\nA use case is a buyer or use of what this market measures, at its value-chain level. Replace them "
              "with in-scope use cases and return a corrected answer in exactly the same shape.\n"
            + f"PREVIOUS ANSWER:\n{json.dumps(prev, ensure_ascii=False)}")
    try:
        again = ask("use_cases", note)
    except Exception as exc:  # noqa: BLE001
        log.append(f"[use_cases] re-ask failed: {exc}")
        again = None
    if isinstance(again, dict) and "error" not in again and any(again.get(r) for r in UC_REGIONS):
        uc = again
    else:
        rec["reask_failed"] = True
    rec["after"] = check_use_cases(uc, definition)
    bad = {(p["dim"], p["segments"][0]) for p in rec["after"]}
    if bad:
        uc = {**uc, **{r: [x for x in uc.get(r) or [] if (r, str((x or {}).get("use_case") or "")) not in bad]
                       for r in UC_REGIONS}}
        rec["removed"] = [f"{r}: {n}" for r, n in sorted(bad)]
        log.append("[use_cases] left out after one re-ask: " + "; ".join(rec["removed"]))
    fields["use_cases"] = uc
    return rec


# --------------------------------------------------------------------------- #
# final verdict (computed by model.build from the final answers)
# --------------------------------------------------------------------------- #
def review(definition: dict, record: dict | None, *, global_problems: list[dict], resolved: dict,
           mix_problems: list[dict], uc_problems: list[dict], copied: bool = False,
           mea_names_mismatch: bool = False) -> dict:
    """m["segment_review"]: every check on the answers the model was built
    from, and what happened to each failure. A check that failed with no
    re-ask on record (data from before PLAN-04) is a blocker; a mix still
    beyond x2 after its re-ask is a warning (no blending)."""
    record = record or {}
    blockers, warnings = [], []
    chk = definition.get("check")
    if not definition.get("current"):
        blockers.append("definition: predates PLAN-04 (no measures / value-chain level / included-excluded lists)")
    elif chk and chk.get("problems"):
        blockers.append("definition: " + "; ".join(chk["problems"]) + " (still failing after one re-ask)")

    grec = record.get("segments_global") or {}
    if global_problems and not grec.get("reasked"):
        blockers.append("segments: " + "; ".join(p["detail"] for p in global_problems) + " (not re-asked)")
    else:
        for p in global_problems:
            if p["kind"] in ("no_definition", "not_included"):
                warnings.append(f"segments ({p['dim']}): {p['detail']} (kept after one re-ask)")
    for dim in resolved.get("all_excluded") or []:
        blockers.append(f"segments ({dim}): every segment is outside the definition")

    mrec = record.get("segments_mea") or {}
    for p in mix_problems:
        line = f"MEA {p['dim']} mix: {p['detail']}" + (f" -- agent's reason: {p['mea_reason']}" if p.get("mea_reason")
                                                         else "")
        if mrec.get("reasked"):
            warnings.append(line + " (kept after one re-ask; not blended)")
        else:
            blockers.append(line + " (not re-asked)")

    if mea_names_mismatch:
        blockers.append("MEA type mix: segments_mea does not use the global type names (re-run segments_mea)")
    if copied or mrec.get("copied_first"):
        outcome = ("still copied after one re-ask" if copied and mrec.get("reasked") else
                   "not re-asked" if copied else "first answer; the re-ask gave country-specific mixes")
        warnings.append(f"MEA mixes copy the global mix ({outcome})")

    urec = record.get("use_cases") or {}
    for p in uc_problems:
        blockers.append(f"use cases ({p['dim']}): {p['detail']}" + ("" if urec.get("reasked") else " (not re-asked)"))

    return {"definition": {"measures": definition.get("measures"), "value_chain_level": definition.get("value_chain_level"),
                           "check": chk},
            "segments_global": {"first_problems": grec.get("problems") or [], "reasked": bool(grec.get("reasked")),
                                "replacement": grec.get("replacement"),
                                "changes": grec.get("changes") or [], "problems": global_problems},
            "resolved": {"dropped": {d: [f"{n} ({why})" for n, why in v] for d, v in resolved["dropped"].items()},
                         "merged": {d: {a: b for a, b in v.items() if a != b} for d, v in resolved["merge"].items() if v},
                         "type_off": resolved.get("type_off")},
            "segments_mea": {"first_problems": mrec.get("problems") or [], "reasked": bool(mrec.get("reasked")),
                             "changes": mrec.get("changes") or [], "problems": mix_problems,
                             "copied_first": bool(mrec.get("copied_first")), "copied": copied},
            "use_cases": {"first_problems": urec.get("problems") or [], "reasked": bool(urec.get("reasked")),
                          "removed": urec.get("removed") or [], "problems": uc_problems},
            "changes": resolved.get("notes") or [],
            "blockers": blockers, "warnings": warnings}
