"""The Number Register: every number the report may print, one row per fact,
with an ID, an exact description, the entity it belongs to and its single
display format (fmt.py). Built from the numeric model before any text is
written; the text, tables and the final sweep are checked against it.

FactGuard checks a sentence (or table row) number by number: the number must
be the display value of a register fact whose entity is named in the same
sentence -- so "Africa's 4.3% share" passes only if 4.3% is a share fact OF
Africa, and a share of the region is never confused with a share of global.
"""
from __future__ import annotations

import hashlib
import re

from AiAgentsFleet.cmi_report import fmt
from AiAgentsFleet.cmi_report.model import Y0, Y1

GLOBAL_WORDS = ["global", "world", "worldwide", "overall", "market", "total"]


def _code(s: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", s.upper())[:10] or "X"


def _code_namer():
    """A collision-safe `_code` for one register: two different names that truncate to the same
    code ('Ascorbic Acid (pure)' / 'Ascorbic Acid derivatives') get a short hash of the full name."""
    by_code: dict[str, str] = {}
    done: dict[str, str] = {}

    def code(s: str) -> str:
        s = s or ""
        if s not in done:
            base = _code(s)
            owner = by_code.setdefault(base, s)
            done[s] = base if owner == s else f"{base}{hashlib.sha1(s.encode('utf-8')).hexdigest()[:4].upper()}"
        return done[s]
    return code


def build_register(m: dict, ag: dict) -> list[dict]:
    reg: list[dict] = []
    _code = _code_namer()                 # noqa: F841 -- shadows the module-level _code inside this register
    used: dict[str, int] = {}

    def add(fid, fact, entities, display, metric):
        # the same fact listed twice (a driver also given as a restraint) still gets its own ID
        used[fid] = used.get(fid, 0) + 1
        if used[fid] > 1:
            fid = f"{fid}-{used[fid]}"
        reg.append({"id": fid, "fact": fact, "entities": entities, "display": display, "metric": metric})

    def node(prefix, label, ents, n, parent=None, parent_label=None, glob=None):
        add(f"{prefix}-{Y0}", f"{label} market size, {Y0}E", ents, fmt.usd(n["v26"]), "value")
        add(f"{prefix}-{Y1}", f"{label} market size, {Y1}F", ents, fmt.usd(n["v33"]), "value")
        add(f"{prefix}-CAGR", f"{label} CAGR {Y0}-{Y1}", ents, fmt.pct(n["cagr"]), "cagr")
        add(f"{prefix}-INC", f"{label} incremental value {Y0}-{Y1}", ents, fmt.incr(n["incr"]), "incr")
        if "v25" in n:
            add(f"{prefix}-{Y0 - 1}", f"{label} market size, {Y0 - 1}", ents, fmt.usd(n["v25"]), "value")
        for y, v in (n.get("series") or {}).items():
            if int(y) not in (Y0, Y1, Y0 - 1):
                add(f"{prefix}-{y}", f"{label} market size, {y}", ents, fmt.usd(v), "value")
        if parent:
            for yk, yl in (("v26", f"{Y0}E"), ("v33", f"{Y1}F")):
                add(f"{prefix}-SHP-{yl}", f"{label} share of {parent_label}, {yl}", ents,
                    fmt.share(n[yk], parent[yk]), "share_parent")
        if glob and parent is not glob:
            for yk, yl in (("v26", f"{Y0}E"), ("v33", f"{Y1}F")):
                add(f"{prefix}-SHG-{yl}", f"{label} share of global market, {yl}", ents,
                    fmt.share(n[yk], glob[yk]), "share_global")
        if glob:
            gap = n["cagr"] - glob["cagr"]
            add(f"{prefix}-GAP", f"{label} CAGR minus global CAGR", ents, fmt.signed(gap), "gap_pp")

    g = m["global"]
    node("G", "Global", ["global"] + GLOBAL_WORDS, g)
    add("G-MULT", f"Global market multiple {Y1}F / {Y0}E", ["global"] + GLOBAL_WORDS,
        f"{g['v33'] / g['v26']:.1f}x", "multiple")
    for r, v in m["regions"].items():
        node(f"R-{_code(r)}", r, [r] + (["MEA"] if r == "Middle East & Africa" else []), v, g, "global market", g)
    mea = m["regions"]["Middle East & Africa"]
    for r, v in m["subregions"].items():
        node(f"SR-{_code(r)}", r, [r], v, mea, "Middle East & Africa", g)
        gap = v["cagr"] - mea["cagr"]
        add(f"SR-{_code(r)}-GAPMEA", f"{r} CAGR minus MEA CAGR", [r], fmt.signed(gap), "gap_pp")
    for region, d in m["countries"].items():
        parent = m["regions"].get(region) or m["subregions"].get(region)
        for c, v in d.items():
            node(f"C-{_code(c)}", c, [c] + ([ "KSA"] if c == "Saudi Arabia" else []), v, parent, region)
        for c, ser in (m.get("country_series") or {}).get(region, {}).items():
            for y, val in ser.items():
                if int(y) not in (Y0, Y1):
                    add(f"C-{_code(c)}-{y}", f"{c} market size, {y}", [c], fmt.usd(val), "value")
    for geo, dims in m["segments"].items():
        total = g if geo == "Global" else (m["subregions"].get(geo) or
                                           next((d[geo] for d in m["countries"].values() if geo in d), None))
        for dim, segs in dims.items():
            if not m["sections"][dim]["show"]:
                continue
            for s, v in segs.items():
                node(f"S-{_code(geo)}-{_code(s)}", f"{geo} {m['dimensions'][dim]['singular']}: {s}", [s], v,
                     total, geo)
    for name, sc in m["scenarios"].items():
        add(f"SC-{_code(name)}-CAGR", f"{name} scenario CAGR {Y0}-{Y1}", [name, f"{name.lower()} scenario"],
            fmt.pct(sc["cagr"]), "cagr")
        add(f"SC-{_code(name)}-{Y1}", f"{name} scenario market size, {Y1}F", [name, f"{name.lower()} scenario"],
            fmt.usd(sc["series"][Y1]), "value")
    # sourced vendor shares only (empty when the report shows a positioning table), all at the one vendor year
    vy = m.get("vendor_year", Y0 - 1)
    for label, rows in (("global", m["vendor_chart_global"]), ("MEA", m["vendor_chart_mea"]),
                        ("global (detail)", m["vendor_share_global"]), ("MEA (detail)", m["vendor_share_mea"])):
        for n, s in rows:
            ents = [n, n.split(" (")[0]] + (["other", "regional"] if "other" in n.lower() else [])
            add(f"V-{_code(label)}-{_code(n)}-{vy}", f"{n} vendor share, {label}, {vy}", ents, fmt.pct(s), "share")
    dyn = ag.get("dynamics") or {}
    if dyn.get("impact_basis") != "score":          # relative scores (rules.reconcile_drivers b) carry no pp
        what = ("impact on MEA CAGR vs the global CAGR baseline" if dyn.get("impact_basis") == "pp_vs_global"
                else "impact on MEA CAGR")
        for d in (dyn.get("mea_drivers") or []) + (dyn.get("mea_restraints") or []):
            add(f"D-{_code(d.get('name', ''))}", f"{d.get('name')} {what} (CMI assessment)",
                [d.get("name", "")], fmt.signed(float(d.get("impact_pp") or 0)), "gap_pp")
    for st in m.get("value_chain") or []:
        for k in ("share_low_pct", "share_high_pct"):
            if st.get(k) is not None:
                add(f"VC-{_code(st.get('stage', ''))}-{k[6:9].upper()}", f"{st.get('stage')} share of value pool ({k[6:9]})",
                    [st.get("stage", "")], f"{float(st[k]):.0f}%", "share")
    for p in m.get("published_2025") or []:
        add(f"P-{_code(p.get('publisher', ''))}", f"{p.get('publisher')} published {Y0 - 1} global estimate",
            [p.get("publisher", "")],                      # the publisher must be named with its figure
            fmt.usd(float(p["value_usd_bn"]) * 1000), "published")
    ids = [e["id"] for e in reg]
    assert len(ids) == len(set(ids)), f"duplicate register IDs: {sorted({i for i in ids if ids.count(i) > 1})}"
    return reg


# --------------------------------------------------------------------------- #
# FactGuard
# --------------------------------------------------------------------------- #
_MONEY = re.compile(r"[+-]?\$\s?\d[\d,]*(?:\.\d+)?\s?[BM]\b")
_PCT = re.compile(r"(?<![\d.])\d+(?:\.\d+)?%")
_PP = re.compile(r"(?<![\d.])[+\-−]?\d+(?:\.\d+)?\s?(?:pp\b|percentage[- ]points?)", re.I)
# the direction a sentence gives a pp gap: '1.2 pp below' / 'subtracts 1.2 pp' = negative
_PP_DOWN_AFTER = re.compile(r"^\W*(?:(?:of|in)\s+\w+\s+)?(?:below|lower|less|slower|behind|under|short of)\b", re.I)
_PP_UP_AFTER = re.compile(r"^\W*(?:(?:of|in)\s+\w+\s+)?(?:above|higher|more|faster|ahead|over)\b", re.I)
_PP_DOWN_BEFORE = re.compile(r"\b(?:subtract\w*|reduc\w*|cuts?|cutting|drags?|dragging|lowers?|lowering|weighs?|"
                             r"weighing|lags?|lagging|trails?|trailing|minus|shaves?|shaving|removes?|removing)\b"
                             r"[\w\s,]{0,20}$", re.I)
_PP_UP_BEFORE = re.compile(r"\b(?:adds?|adding|boosts?|boosting|lifts?|lifting|raises?|raising|contribut\w*|plus|"
                           r"premium of)\b[\w\s,]{0,20}$", re.I)


def pp_sign(token: str, before: str, after: str) -> int | None:
    """+1 / -1 for the direction a sentence gives a pp figure, None when it doesn't say."""
    t = token.strip()
    if t[:1] in "-−":
        return -1
    if t[:1] == "+":
        return 1
    for rx, sign in ((_PP_DOWN_AFTER, -1), (_PP_UP_AFTER, 1)):
        if rx.search(after):
            return sign
    for rx, sign in ((_PP_DOWN_BEFORE, -1), (_PP_UP_BEFORE, 1)):
        if rx.search(before):
            return sign
    return None
_MULT = re.compile(r"(?<![\d.])\d+(?:\.\d+)?x\b")
# bare numbers, but not identifiers such as "Regulation 2019/1009"
_BARE = re.compile(r"(?<![\w$.+/-])\d[\d,]*(?:\.\d+)?(?![\d%xBM/])")


def count_numbers(text: str) -> int:
    """How many data numbers a text holds (for the verification log)."""
    rest = text
    n = 0
    for rx in (_MONEY, _PP, _PCT, _MULT):
        n += len(rx.findall(rest))
        rest = rx.sub(" ", rest)
    return n + sum(1 for mm in _BARE.finditer(rest) if "." in mm.group())


def _norm_money(t: str) -> str:
    return t.replace(" ", "").lstrip("+-")


class FactGuard:
    """Same interface as guard.NumberGuard (.bad(sentence) -> problems)."""

    def __init__(self, register: list[dict], extra_ok: list[str] | None = None):
        self.by_display: dict[str, list[dict]] = {}
        for e in register:
            key = _norm_money(e["display"]).lstrip("+")
            self.by_display.setdefault(key, []).append(e)
        self.extra = {x for x in (extra_ok or [])}

    @staticmethod
    def _named(entry: dict, text: str) -> bool:
        low = text.lower()
        return any(n and re.search(rf"(?<![a-z]){re.escape(n.lower())}(?![a-z])", low) for n in entry["entities"])

    def _check(self, token: str, key: str, text: str, kinds: set[str] | None = None,
               sign: int | None = None) -> str | None:
        cands = [e for e in self.by_display.get(key, []) if not kinds or e["metric"] in kinds]
        if not cands:
            return f"{token} is not a register value"
        if sign is not None:
            # a signed gap: '1.2 pp below' must be a -1.2 fact, '+1.2 pp' / '1.2 pp above' a +1.2 one
            same = [e for e in cands if float(e["display"]) * sign >= 0]
            if not same:
                return f"{token} has the wrong sign: the register value is {cands[0]['display']} ({cands[0]['fact']})"
            cands = same
        named = [e for e in cands if self._named(e, text)]
        if not named:
            return f"{token} belongs to {cands[0]['id']} ({cands[0]['fact']}), whose entity is not named here"
        if "%" in token:
            says_global = re.search(r"\bof (?:the )?(?:global|world|worldwide)\b", text, re.I)
            if says_global and all(e["metric"] == "share_parent" and "global" not in e["fact"] for e in named) \
                    and not any(e["metric"] in ("cagr", "share", "share_global") for e in named):
                return f"{token} is a share of the parent region, but the sentence says 'of global'"
        return None

    def bad(self, text: str) -> list[str]:
        problems = []
        rest = text
        for mm in _MONEY.finditer(text):
            p = self._check(mm.group(), _norm_money(mm.group()), text)
            if p:
                problems.append(p)
        rest = _MONEY.sub(" ", rest)
        for mm in _PP.finditer(rest):
            val = re.search(r"\d+(?:\.\d+)?", mm.group()).group()
            sign = pp_sign(mm.group(), rest[max(0, mm.start() - 40): mm.start()], rest[mm.end(): mm.end() + 30])
            p = self._check(mm.group(), f"{float(val):.1f}", text, {"gap_pp"}, sign)
            if p:
                problems.append(p)
        rest = _PP.sub(" ", rest)
        for mm in _PCT.finditer(rest):
            if mm.group() in self.extra:
                continue
            p = self._check(mm.group(), mm.group(), text)
            if p:
                problems.append(p)
        rest = _PCT.sub(" ", rest)
        for mm in _MULT.finditer(rest):
            p = self._check(mm.group(), mm.group(), text, {"multiple"})
            if p:
                problems.append(p)
        rest = _MULT.sub(" ", rest)
        # bare numbers: years, small counts and 1-5 scores are fine; anything else must be a register value
        for mm in _BARE.finditer(rest):
            s = mm.group().replace(",", "")
            try:
                x = float(s)
            except ValueError:
                continue
            if ("." not in s and (x <= 50 or 1900 <= x <= 2040)) or s in self.extra:
                continue
            if not any(e["display"].strip("+-$BM%x ").replace(",", "") == s for es in self.by_display.values()
                       for e in es):
                problems.append(f"{s} is not a register value")
        return problems
