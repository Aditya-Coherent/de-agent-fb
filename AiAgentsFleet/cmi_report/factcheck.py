"""Fact-identity check: one fact = one number, everywhere.

For every number in the report's text and country tables it works out WHICH
fact the number states -- the entity (nearest name before it in the clause),
the kind of fact (CAGR, share, market value, incremental value, pp gap) and,
for values, the year -- and compares it with the Number Register's value for
exactly that fact. It then lists every fact stated more than once and checks
all its occurrences show the identical number.

A number whose fact cannot be pinned down (no entity or no fact keyword
nearby) is left to the register sweep, which already guarantees it is a
register value of an entity named in the same sentence.
"""
from __future__ import annotations

import re

from AiAgentsFleet.cmi_report.guard import sentences
from AiAgentsFleet.cmi_report.register import _MONEY, _PCT, _PP, _norm_money

_CLAUSE = re.compile(r";|,\s*(?:while|whereas|but|and)\b|\bwhile\b|\bwhereas\b")
_CAGR_WORDS = re.compile(r"\b(cagr|grow\w*|growth|expand\w*|a year|annual\w*|per year|rate)\b", re.I)
_SHARE_WORDS = re.compile(r"\b(share|of (?:the )?(?:global|market|regional|region|sub-region|total|value)|"
                          r"accounts? for|holds?|represent\w*|of [A-Z][a-z]+ (?:value|market))\b", re.I)
_INCR_WORDS = re.compile(r"\b(incremental|add\w*|opportunit\w*|gain\w*|increase of|uplift)\b", re.I)
_YEAR = re.compile(r"\b(20[2-3]\d)[EF]?\b")


def _kind(entry: dict) -> tuple[str, str | None]:
    m = entry["metric"]
    if m == "value":
        y = _YEAR.search(entry["fact"])
        return "value", y.group(1) if y else None
    if m in ("share_parent", "share_global", "share"):
        return "share", None
    if m == "gap_pp":
        return "gap", None
    return m, None                                  # cagr, incr, multiple, published


def build_index(register: list[dict]) -> tuple[dict, list[str]]:
    """{(entity_lower, kind, year): {displays}} and the entity names, longest first."""
    idx: dict[tuple, set[str]] = {}
    names: set[str] = set()
    for e in register:
        kind, year = _kind(e)
        for n in e["entities"]:
            if not n or n.lower() in ("market", "total", "overall", "worldwide", "world"):
                continue
            names.add(n)
            idx.setdefault((n.lower(), kind, year), set()).add(_norm_money(e["display"]).lstrip("+"))
    return idx, sorted(names, key=len, reverse=True)


def _entity_before(clause: str, pos: int, names: list[str]) -> str | None:
    """The entity named closest before the number: the match that ENDS latest wins, and among
    matches ending at the same place the longest ('Middle East & Africa', not 'Africa')."""
    best, best_key = None, (-1, 0)
    low = clause.lower()
    for n in names:
        for mm in re.finditer(rf"(?<![a-z]){re.escape(n.lower())}(?![a-z])", low[:pos]):
            key = (mm.end(), len(n))
            if key > best_key:
                best, best_key = n, key
    if best is None and re.search(r"\b(global|world|overall market|the market)\b", low[:pos]):
        return "global"
    return best


_YEAR_AFTER = re.compile(r"\s*(?:\(\s*|,\s*)?(?:(?:in|by|for|of|during|through|until)\s+)?(?:the\s+year\s+)?"
                         r"(20[2-3]\d)[EF]?\b", re.I)


def _year_near(clause: str, pos: int, end: int) -> str | None:
    """The year a number belongs to. A year attached directly after it ('$X by 2033F', '$X in 2026E',
    '$X (2026E)') wins; otherwise the nearest year BEFORE it in the clause ('in 2026E at $X and in 2033F
    at $Y': $X is 2026E, although 2033F follows a few characters later); a later year counts only when
    the clause has no year before the number."""
    after = _YEAR_AFTER.match(clause, end)
    if after:
        return after.group(1)
    before = [m.group(1) for m in _YEAR.finditer(clause[:pos])]
    if before:
        return before[-1]
    later = _YEAR.search(clause[end:end + 14])
    return later.group(1) if later else None


def entities_in(clause: str, names: list[str]) -> list[str]:
    """Every entity named in the clause; a name inside a longer named one ('Africa' in
    'Middle East & Africa') does not count separately."""
    low = clause.lower()
    spans = []
    for n in names:
        for mm in re.finditer(rf"(?<![a-z]){re.escape(n.lower())}(?![a-z])", low):
            spans.append((mm.start(), mm.end(), n))
    keep = [sp for sp in spans if not any(o[0] <= sp[0] and sp[1] <= o[1] and (o[1] - o[0]) > (sp[1] - sp[0])
                                          for o in spans)]
    found = list(dict.fromkeys(n for _, _, n in keep))
    if re.search(r"\b(global|world|overall market|the market)\b", low) and "global" not in found:
        found.append("global")
    return found


_GLOBAL_BEFORE = re.compile(r"\b(?:the\s+)?(?:global|world)\s*$", re.I)


def _own_entities(clause: str, names: list[str], prev: str | None) -> list[str]:
    """entities_in(clause), but a clause naming no entity of its own keeps the sentence's subject."""
    ent = entities_in(clause, names)
    if prev and not [e for e in ent if e != "global"]:
        return [prev]
    return ent


def mentions(text: str, names: list[str]):
    """Yields (token, display_key, entity, kind, year, clause) for each pin-downable number."""
    for sent in sentences(text):
        prev = None                     # the sentence's subject, carried into later clauses
        # only a sentence that STARTS with its entity has it as the subject ('The Middle East & Africa is
        # ..., but grows to $175M'); in 'Humic Substances are the largest segment in the Middle East ...,
        # forecast to reach $52.1M' the region is not the subject, so nothing is carried
        lead = re.sub(r"^\s*(?:the\s+)?", "", sent.lower())
        subject = next((n for n in sorted(names, key=len, reverse=True)
                        if lead.startswith(n.lower()) and n != "global"), None)
        for clause in _CLAUSE.split(sent):
            named_here = [subject] if subject else []
            money = list(_MONEY.finditer(clause))
            years = sorted({y for y in _YEAR.findall(clause)})
            # "at $X and $Y (respectively) in 2026E and 2033F" / "from $X to $Y": values pair with years in order
            paired = (len(years) >= 2 and len([m for m in money if not m.group().startswith("+")]) == 2
                      and re.search(r"\b(respectively|from\b.*\bto|both)\b", clause, re.I))
            plain = [m for m in money if not m.group().startswith("+")]
            for mm in money:
                ent = _own_entities(clause, names, prev)
                if not ent:
                    continue
                if paired and mm in plain and not _INCR_WORDS.search(clause[max(0, mm.start() - 30): mm.start()]):
                    yield mm.group(), _norm_money(mm.group()), ent, "value", years[plain.index(mm)], clause
                    continue
                before = clause[max(0, mm.start() - 30): mm.start()]
                tok = mm.group()
                if tok.startswith("+") or _INCR_WORDS.search(before):
                    yield tok, _norm_money(tok).lstrip("+"), ent, "incr", None, clause
                else:
                    yield tok, _norm_money(tok), ent, "value", _year_near(clause, mm.start(), mm.end()), clause
            for mm in _PCT.finditer(clause):
                ent = _own_entities(clause, names, prev)
                if not ent:
                    continue
                if _GLOBAL_BEFORE.search(clause[:mm.start()]):
                    ent = ["global"]
                win = clause[max(0, mm.start() - 45): mm.end() + 15]
                if _CAGR_WORDS.search(win) and not re.search(r"\bshare\b", win, re.I):
                    kind = "cagr"
                elif _SHARE_WORDS.search(win):
                    kind = "share"
                else:
                    continue
                yield mm.group(), mm.group(), ent, kind, None, clause
            for mm in _PP.finditer(clause):
                ent = entities_in(clause, names)
                if ent:
                    val = re.search(r"\d+(?:\.\d+)?", mm.group()).group()
                    yield mm.group(), f"{float(val):.1f}", ent, "gap", None, clause
            if named_here:
                prev = named_here[0]


def check(register: list[dict], texts: list[str], tables: list[list[list[str]]]) -> dict:
    idx, names = build_index(register)
    mismatches, seen = [], {}

    def expected(e, kind, year):
        if kind == "value" and year:
            return idx.get((e.lower(), kind, year))
        if kind == "value":
            vals = [v for (n, k, y), v in idx.items() if n == e.lower() and k == "value"]
            return set().union(*vals) if vals else None
        return idx.get((e.lower(), kind, None))

    publishers = {n for (n, k, y) in idx if k == "published"}

    def record(ents, kind, year, key, where):
        ents = [ents] if isinstance(ents, str) else ents
        # a value attributed to a named publisher is that publisher's figure, not CMI's estimate
        named_pubs = [e for e in ents if e.lower() in publishers]
        if named_pubs and kind == "value":
            pub_vals = set().union(*[idx[(p.lower(), "published", None)] for p in named_pubs])
            if key not in pub_vals:
                mismatches.append(f"{' / '.join(named_pubs)} published figure: report says {key}, the publisher's "
                                  f"figure is {' / '.join(sorted(pub_vals))} -- {where[:150]}")
            return
        exps = {e: expected(e, kind, year) for e in ents}
        exps = {e: x for e, x in exps.items() if x}
        if not exps:
            return
        if not any(key in x for x in exps.values()):
            mismatches.append(f"{' / '.join(exps)} {kind}{' ' + year if year else ''}: report says {key}, "
                              f"register has {' / '.join(sorted(set().union(*exps.values())))[:80]} -- {where[:150]}")
        named = [e for e in exps if e != "global"] or list(exps)
        if len(named) == 1 and len(exps[named[0]]) == 1:   # unambiguous single fact: track its occurrences
            seen.setdefault((named[0], kind, year), {}).setdefault(key, []).append(where[:90])

    for t in texts:
        for tok, key, ent, kind, year, clause in mentions(t, names):
            record(ent, kind, year, key, clause.strip())
    # country tables: Country | 2026E | 2033F | CAGR | Incremental $
    for tb in tables:
        if not tb or [c.strip() for c in tb[0][:5]] != ["Country", "2026E", "2033F", "CAGR", "Incremental $"]:
            continue
        for row in tb[1:]:
            ent = row[0].replace(" total", "")
            for col, kind, year in ((1, "value", "2026"), (2, "value", "2033"), (3, "cagr", None), (4, "incr", None)):
                if col < len(row) and row[col]:
                    key = _norm_money(row[col]).lstrip("+") if kind != "cagr" else row[col]
                    record(ent, kind, year, key, f"table row: {' | '.join(row)}")
    repeated = {f"{e} {k}{' ' + y if y else ''}": vals for (e, k, y), vals in seen.items()
                if sum(len(v) for v in vals.values()) > 1}
    inconsistent = {f: v for f, v in repeated.items() if len(v) > 1}
    return {"mismatches": mismatches,
            "facts_stated_more_than_once": len(repeated),
            "occurrences_of_repeated_facts": sum(sum(len(x) for x in v.values()) for v in repeated.values()),
            "inconsistent_repeated_facts": inconsistent}
