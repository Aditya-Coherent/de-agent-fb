"""Sentence-level checks that keep generated text faithful to the model:

  * NumberGuard -- every number in a sentence must be one of the model's
    facts (as formatted by fmt.py, any rounding of it) or a year / small count;
  * ClaimGuard  -- a sentence that calls something the largest / fastest /
    smallest / slowest / biggest opportunity must be naming the entity that
    really is, within that entity's own peer group; a near-tie (rules.py)
    must be called a tie, and an ordinal ('second-largest') must be right;
  * contradiction_guard -- comparative and causal sentences ('faster than',
    'momentum', 'more concentrated', a 'high X' that a restraint calls
    limited) must agree with the model (PLAN-07).

A sentence that fails is dropped (not the whole paragraph).
"""
from __future__ import annotations

import re

from AiAgentsFleet.cmi_report import rules

_NUM = re.compile(r"(?<![A-Za-z\d.])\d[\d,]*(?:\.\d+)?")
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def numbers(text: str) -> list[tuple[float, int]]:
    out = []
    for m in _NUM.finditer(text or ""):
        s = m.group().replace(",", "").rstrip(".")
        try:
            out.append((float(s), len(s.split(".")[1]) if "." in s else 0))
        except ValueError:
            pass
    return out


def collect(obj, out: list[float]) -> list[float]:
    if isinstance(obj, dict):
        for v in obj.values():
            collect(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            collect(v, out)
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        out.append(float(obj))
    elif isinstance(obj, str):
        out.extend(v for v, _ in numbers(obj))
    return out


# A full stop after these is not the end of a sentence ("Syngenta (incl. Valagro)",
# "Charoen Pokphand Foods Public Co. Ltd", "e.g. Kenya", "U.S. growers").
_ABBREV = re.compile(r"\b(?:incl|Co|Ltd|Inc|Corp|Plc|PLC|e\.g|i\.e|vs|approx|est|No|St|Mt|Dr|Jr|Sr|U\.S|U\.K|"
                     r"U\.A\.E|S\.A|N\.V|B\.V|A\.G|S\.p\.A|Pty|Pvt|Bhd|Tbk|etc|cf|al|[A-Z](?:\.[A-Z])+)\.$")


def sentences(text: str) -> list[str]:
    parts = [s for s in _SENT.split((text or "").strip()) if s.strip()]
    out: list[str] = []
    for s in parts:
        # re-join a split made right after an abbreviation
        if out and _ABBREV.search(out[-1]):
            out[-1] = out[-1] + " " + s
        else:
            out.append(s)
    return out


class NumberGuard:
    def __init__(self, facts, extra_ok: list[str] | None = None):
        vals = collect(facts, [])
        for e in extra_ok or []:
            vals += [v for v, _ in numbers(e)]
        self.vals = sorted(set(vals) | {v / 1000 for v in vals} | {v * 1000 for v in vals})

    def ok_number(self, x: float, dec: int) -> bool:
        if dec == 0 and (x <= 12 or 1900 <= x <= 2040):     # small counts, years (incl. law years)
            return True
        tol = 0.5 * 10 ** (-dec) + 1e-9
        return any(abs(x - a) <= tol for a in self.vals)

    def bad(self, sentence: str) -> list[str]:
        return [f"{x:g}" for x, d in numbers(sentence) if not self.ok_number(x, d)]


# --------------------------------------------------------------------------- #
# comparative claims
# --------------------------------------------------------------------------- #
_SUPER = {
    "size_max": r"\b(largest|biggest|dominant|dominates|dominate|leads(?! to)|leader)\b",
    "size_min": r"\b(smallest)\b",
    "growth_max": r"\b(fastest|fastest-growing|quickest|highest (?:growth|cagr))\b",
    "growth_min": r"\b(slowest|lowest (?:growth|cagr))\b",
}
# 'one of the largest' / 'among the fastest' are checked by ClaimGuard.qualified; ordinals by ClaimGuard.ordinal
_QUALIFIED = re.compile(r"\b(one of|among|next|after)\b[\w\s-]{0,15}$", re.I)
_ORDINAL_BEFORE = re.compile(r"\b(?:second|third|fourth|fifth)[- ]$", re.I)
_ORDINAL = re.compile(r"\b(second|third|fourth|fifth)[- ](largest|biggest|smallest|fastest|slowest)\b", re.I)
_ORDINAL_N = {"second": 2, "third": 3, "fourth": 4, "fifth": 5}
_CLAUSE = re.compile(r";|,\s*(?:but|while|whereas|and)\b|\bwhile\b|\bwhereas\b|\bbut\b")
# a sentence that says two leaders are level may name either of them
_TIE_WORDS = re.compile(r"\b(tie|ties|tied|joint|jointly|equal|level with|alongside|similar|on par|in line with|"
                        r"neck and neck|matching)\b", re.I)


class ClaimGuard:
    """`groups` = [{"names": {name: {"v26", "v33", "incr", "cagr"}}, "aliases": {alias: name}}, ...];
    each group is a set of peers (regions, Africa countries, Egypt's types...)."""

    def __init__(self, groups: list[dict], global_cagr: float | None = None,
                 template_sentences: list[set[str]] | None = None):
        self.groups = groups
        self.global_cagr = global_cagr
        self.template_sentences = template_sentences or []
        self.name_words = {w for g in groups for n in list(g["names"]) + list(g.get("aliases", {}))
                           for w in word_set(n)}
        self.no_breakdown: list[str] = []       # countries without a segment breakdown in the report
        self.segment_names: list[str] = []
        # contradiction_guard inputs (prose.make_guard fills them): the 'Regional & other' vendor
        # share {"global": %, "mea": %}, the MEA restraint names, and names outside the MEA
        self.other_share: dict[str, float] = {}
        self.restraints: list[str] = []
        self.non_mea: list[str] = []
        names = {n for g in groups for n in list(g["names"]) + list(g.get("aliases", {}))}
        # For each group, the longer names of OTHER groups that contain one of its
        # names ('Middle East & Africa' vs 'Africa'), blanked before matching it.
        self._shadow = []
        for g in groups:
            own = {n.lower() for n in list(g["names"]) + list(g.get("aliases", {}))}
            self._shadow.append(sorted({n.lower() for n in names if n.lower() not in own
                                        and any(o in n.lower() for o in own)}, key=len, reverse=True))

    @staticmethod
    def _mentions(clause: str, names: dict, aliases: dict) -> list[str]:
        low = clause.lower()
        found = []
        for n in list(names) + list(aliases):
            key = n.lower().split(" (")[0]
            if re.search(rf"(?<![a-z]){re.escape(key)}(?![a-z])", low):
                found.append(aliases.get(n, n))
        # only names of THIS group (an alias may point into another group), and
        # "Rest of Africa" also contains "Africa": keep the longest names only
        found = [f for f in found if f in names]
        return [f for f in dict.fromkeys(found) if not any(f != g and f.lower() in g.lower() for g in found)]

    def _blank_scopes(self, text: str) -> str:
        """'... largest product type in the UAE' -- the UAE is where the claim
        holds, not what it is about: scope phrases are removed before matching."""
        names = sorted({n for g in self.groups for n in list(g["names"]) + list(g.get("aliases", {}))},
                       key=len, reverse=True)
        alt = "|".join(re.escape(n) for n in names)
        return re.sub(rf"\b(?:in|across|within|for)\s+(?:the\s+)?(?:{alt})\b", " ", text, flags=re.I)

    def qualified(self, sentence: str) -> str | None:
        """'one of the fastest-growing' / 'among the largest' / 'the faster-growing of the two':
        a qualifier is not a way around a ranking -- it must still be true."""
        m = re.search(r"\b(?:one of the|among the)\s+(fast(?:est|er)|larg(?:est|er)|bigg(?:est|er))", sentence, re.I)
        two = re.search(r"\bthe (faster|larger|bigger|smaller|slower)[- ](?:growing )?(?:of the two|sub-?region|half)?",
                        sentence, re.I)
        if not (m or two):
            return None
        text = self._blank_scopes(sentence)
        for g, shadow in zip(self.groups, self._shadow):
            t = text
            for sh in shadow:
                t = re.sub(re.escape(sh), " ", t, flags=re.I)
            hit = self._mentions(t, g["names"], g.get("aliases", {}))
            if len(hit) != 1:
                continue
            d, e = g["names"], hit[0]
            if m:
                key = "cagr" if m.group(1).lower().startswith("fast") else "v26"
                order = sorted(d, key=lambda n: d[n][key], reverse=True)
                top_half = order[: max(1, (len(order) + 1) // 2)]
                if e not in top_half:
                    return f"'{m.group(0)}' for {e}, which ranks {order.index(e) + 1} of {len(order)}"
                if key == "cagr" and self.global_cagr is not None and d[e]["cagr"] <= self.global_cagr \
                        and e not in ("global",):
                    return f"'{m.group(0)}' for {e}, which grows slower than the global market"
            if two and len(d) == 2:
                word = two.group(1).lower()
                key = "cagr" if word in ("faster", "slower") else "v26"
                best = (max if word in ("faster", "larger", "bigger") else min)(d, key=lambda n: d[n][key])
                if best != e:
                    return f"'the {word}' names {e} but it is {best}"
                a, b = (d[n][key] for n in d)
                if rules.tied(key, a, b) and not _TIE_WORDS.search(sentence):
                    return f"'the {word}' for {e}, but the two are within the tie threshold ({a:.1f} vs {b:.1f})"
        return None

    def _hits(self, text: str) -> list[tuple[dict, list[str]]]:
        """(group, names of that group mentioned in `text`) for every group with a hit."""
        out = []
        for g, shadow in zip(self.groups, self._shadow):
            t = text
            for sh in shadow:
                t = re.sub(re.escape(sh), " ", t, flags=re.I)
            hit = self._mentions(t, g["names"], g.get("aliases", {}))
            if hit:
                out.append((g, hit))
        return out

    def ordinal(self, sentence: str) -> str | None:
        """'Europe is the second-largest region' must be true: the entity named
        holds that rank in its peer group (or is tied with the one that does)."""
        size_key = "v33" if re.search(r"\b2033F?\b|\bby 2033\b|\bforecast\b", sentence, re.I) else "v26"
        for clause in _CLAUSE.split(sentence):
            for mm in _ORDINAL.finditer(clause):
                n, word = _ORDINAL_N[mm.group(1).lower()], mm.group(2).lower()
                key = "cagr" if word in ("fastest", "slowest") else \
                    "incr" if re.search(r"opportunit|increment|absolute", clause, re.I) else size_key
                window = self._blank_scopes(clause[max(0, mm.start() - 80): mm.end() + 40])
                for g, hit in self._hits(window):
                    d = g["names"]
                    order = sorted(d, key=lambda k: d[k][key], reverse=word in ("largest", "biggest", "fastest"))
                    if len(order) < n:
                        return f"'{mm.group(0)}' for {hit}, but the peer group has only {len(order)} members"
                    target = order[n - 1]
                    if target in hit or any(rules.tied(key, d[h][key], d[target][key]) for h in hit):
                        continue
                    ranks = ", ".join(f"{h} is #{order.index(h) + 1}" for h in hit)
                    return f"'{mm.group(0)}' names {hit} but #{n} is {target} ({ranks}, {key})"
        return None

    def growth_vs(self, sentence: str, global_cagr: float | None) -> str | None:
        """'X grows faster than the global market' / 'MEA's growth premium' /
        'Y lags the world' must match the CAGRs."""
        if global_cagr is None:
            return None
        up = re.search(r"\b(premium|faster than|outpac\w*|outgrow\w*|ahead of|above (?:the )?(?:global|world|"
                       r"market|overall))", sentence, re.I)
        down = re.search(r"\b(slower than|lag\w*|trail\w*|below (?:the )?(?:global|world|market|overall)|discount)\b",
                         sentence, re.I)
        if not (up or down) or not re.search(r"\b(global|world|overall market|market average)\b", sentence, re.I):
            return None
        text = self._blank_scopes(sentence)
        for g, shadow in zip(self.groups, self._shadow):
            t = text
            for s in shadow:
                t = re.sub(re.escape(s), " ", t, flags=re.I)
            hit = self._mentions(t, g["names"], g.get("aliases", {}))
            if len(hit) == 1:
                v = g["names"][hit[0]]["cagr"]
                if up and v <= global_cagr:
                    return f"says {hit[0]} grows faster than the global market, but {v:.1f}% <= {global_cagr:.1f}%"
                if down and not up and v >= global_cagr:
                    return f"says {hit[0]} grows slower than the global market, but {v:.1f}% >= {global_cagr:.1f}%"
        return None

    @staticmethod
    def _printed(key: str, v: float) -> str:
        from AiAgentsFleet.cmi_report import fmt
        return fmt.pct(v) if key == "cagr" else fmt.usd(v)

    def problem(self, sentence: str) -> str | None:
        # a size ranking is judged in the year the sentence talks about: 2033 only when it says so
        size_key = "v33" if re.search(r"\b2033F?\b|\bby 2033\b|\bforecast\b", sentence, re.I) else "v26"
        tie_words = _TIE_WORDS.search(sentence)
        for clause in _CLAUSE.split(sentence):
            for kind, pat in _SUPER.items():
                m = re.search(pat, clause, re.I)
                # 'second-largest' is an ordinal (ClaimGuard.ordinal), not a 'largest' claim
                if not m or _QUALIFIED.search(clause[: m.start()]) or _ORDINAL_BEFORE.search(clause[: m.start()]):
                    continue
                # the claim's subject sits near the superlative ('X is the largest', 'largest is X')
                window = self._blank_scopes(clause[max(0, m.start() - 80): m.end() + 40])
                for g, shadow in zip(self.groups, self._shadow):
                    text = window
                    for s in shadow:
                        text = re.sub(re.escape(s), " ", text, flags=re.I)
                    hit = self._mentions(text, g["names"], g.get("aliases", {}))
                    if not hit:
                        continue
                    d = g["names"]
                    # 'smallest region after X' / 'largest market after X' = second in the ranking
                    if kind.startswith("growth"):
                        key = "cagr"
                    elif kind == "size_max" and re.search(r"opportunit|increment|absolute|add", clause, re.I):
                        key = "incr"
                    else:
                        key = size_key
                    order = sorted(d, key=lambda n: d[n][key], reverse=kind.endswith("max"))
                    # 'smallest region after X' / 'largest market after X' = second in the ranking
                    if re.match(r"[\w\s-]{0,25}?\bafter\b", clause[m.end():]):
                        if order[1] not in hit:
                            return f"'{m.group()} ... after' names {hit} but second is {order[1]}"
                        continue
                    if order[0] not in hit:
                        # 'A and B grow at a similar rate, the fastest ...' may name the tied runner-up
                        if not (tie_words and any(rules.tied(key, d[h][key], d[order[0]][key]) for h in hit)):
                            return f"'{m.group()}' names {hit} but it is {order[0]} ({key})"
                        continue
                    # a near-tie (rules.TIE_PP / TIE_REL) must be described as a tie
                    if len(order) > 1 and not tie_words and rules.tied(key, d[order[0]][key], d[order[1]][key]):
                        return (f"'{m.group()}' for {order[0]}, but it is level with {order[1]} "
                                f"({self._printed(key, d[order[0]][key])} vs {self._printed(key, d[order[1]][key])})")
        return None


# Draft/template caveats and disclaimers copied from the template never belong in a report.
_BANNED = re.compile(r"\b(refresh(ed)? (all |these )?(values|data|figures)|before (external )?(release|publication)|"
                     r"proprietary database|draft|this template|publication template|disclaimer|"
                     r"for information purposes)\b", re.I)


def word_set(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", s.lower()) if len(w) > 3}


_GENERIC_WORDS = {"market", "markets", "global", "growth", "grow", "grows", "growing", "forecast", "region",
                  "regional", "regions", "value", "share", "estimate", "estimates", "while", "with", "from", "that",
                  "this", "their", "through", "than", "across", "over", "into", "most", "more", "largest", "fastest"}


def copied(s: str, template_sentences: list[set[str]], ignore: set[str] = frozenset()) -> bool:
    """A sentence that is mostly a sentence of the template's own (drone-market)
    example text -- copied rather than written for this market. Place names and
    generic market vocabulary don't count (every report shares them)."""
    skip = _GENERIC_WORDS | ignore
    w = word_set(s) - skip
    return len(w) >= 6 and any(len(w & (t - skip)) / len(w) >= 0.7 for t in template_sentences if len(t) >= 6)


# The drone template's signature phrasings: written for one market, they read as boilerplate in every
# other report ("Africa is a volume and services story", "A common theme: localise, bundle and comply").
_TEMPLATE_PHRASES = re.compile(
    r"\b(?:is an? [\w\s,-]{0,35}?story\b|a common theme\b|swing factor|"
    r"depends less on .{3,80}? than on|smallest region(?:al market)? in (?:the|this) study|"
    r"growth premium .{0,60}?persists|permit-by-permit|the (?:drone|uas) ecosystem|"
    r"winners will combine|pure (?:hardware|product|equipment) (?:exporters|importers))", re.I)
_ORPHAN = re.compile(r"^(?:Finding:\s*)?(This|That|These|Those|It|Its|Such|Together|Both|They|Their|"
                     r"The latter|The former)\b", re.I)


def no_breakdown_claim(s: str, countries: list[str], segments: list[str]) -> str | None:
    """A segment claim about a country whose segment breakdown is not in the report."""
    low = s.lower()
    c = next((c for c in countries if re.search(rf"(?<![a-z]){re.escape(c.lower())}(?![a-z])", low)), None)
    seg = next((x for x in segments if len(x) > 3 and x.lower() in low), None)
    return f"segment claim ({seg}) for {c}, whose segment breakdown is not in the report" if c and seg else None


# --------------------------------------------------------------------------- #
# contradictions (PLAN-07): comparative / causal sentences checked against the model
# --------------------------------------------------------------------------- #
_FASTER = re.compile(r"\b(?:grow\w*\s+(?:\w+\s+)?faster than|faster[- ]growing than|outpac\w*|outgrow\w*|"
                     r"grow\w*\s+(?:\w+\s+)?more (?:quickly|rapidly|strongly) than)\b", re.I)
_SLOWER = re.compile(r"\b(?:grow\w*\s+(?:\w+\s+)?(?:slower|more slowly) than|slower[- ]growing than|"
                     r"lags?|lagging|trails?|trailing)\b", re.I)
_MOMENTUM = re.compile(r"\b(momentum|high[- ]growth|fast[- ]growing|rapidly[- ]growing|rapid growth|"
                       r"growth (?:hotspots?|engines?|markets?))\b", re.I)
# vendor concentration in MEA vs globally ('Regional & other' share: lower = more concentrated)
_MORE_CONCENTRATED = re.compile(r"\b(more concentrated|less fragmented|smaller long tail|more consolidated|"
                                r"global (?:brands|leaders|players|majors) (?:hold|take|capture|command) more)\b", re.I)
_LESS_CONCENTRATED = re.compile(r"\b(less concentrated|more fragmented|larger long tail|longer tail|less consolidated|"
                                r"(?:local|regional|domestic)\b[^.;]{0,80}\b(?:more than|outweigh\w*|over)\b[^.;]{0,30}"
                                r"\b(?:global|international|multinational)\b)", re.I)
# a level the restraints contradict: 'high mobile internet penetration' vs 'Limited digital infrastructure'
_LOW_RESTRAINT = re.compile(r"^\s*(?:limited|low|weak|poor|lack of|insufficient|inadequate|scarce|underdeveloped|"
                            r"fragmented)\s+(.+)$", re.I)
_HIGH_CLAIM = r"\b(?:high|strong|widespread|robust|extensive|advanced|well[- ]developed|deep|excellent|good|broad)\s+"
_TOPIC_STOP = {"the", "and", "of", "in", "for", "to", "a", "an", "local", "rural", "market", "markets", "access",
               "level", "levels", "base", "capacity", "networks", "network", "systems", "system", "sector",
               "consumer", "consumers", "farmer", "farmers", "some", "countries", "regional"}
_SYNONYMS = [{"digital", "internet", "mobile", "connectivity", "broadband", "smartphone", "online", "data"},
             {"awareness", "knowledge", "literacy", "expertise", "skills", "know-how"},
             {"distribution", "logistics", "cold-chain", "cold", "supply"}]
# a tightening of market-entry rules is a barrier, not a demand driver (FINDINGS B7)
_TIGHT_RULES = re.compile(r"\b(?:tight\w*|strict\w*|stringent|tougher)\s+(?:\w+\s+){0,3}?(?:registration|"
                          r"approval|licen[cs]\w*|permit\w*)\b|\b(?:registration|approval|licen[cs]\w*|permit\w*)\s+"
                          r"(?:\w+\s+){0,3}?(?:tighten\w*|stricter)\b", re.I)
_DEMAND_UP = re.compile(r"\b(?:sustain\w*|support\w*|boost\w*|driv\w*|underpin\w*|fuel\w*|lift\w*|spur\w*)\s+"
                        r"(?:\w+\s+){0,2}?demand\b", re.I)


def contradiction_guard(sentence: str, cg: "ClaimGuard") -> str | None:
    """Why `sentence` contradicts the model, or None. Checks (FINDINGS B2, C11, D4, B7):
      * 'X grows faster than Y' / 'X outpaces Y' between two named peers;
      * 'momentum' / 'high-growth' / 'fast-growing' for an entity that does not grow
        faster than its parent (its peer average);
      * 'more concentrated' / 'local players shape share more than global brands' in MEA
        vs the 'Regional & other' vendor share in MEA and globally;
      * a 'high X' level that an MEA restraint calls limited / low / weak;
      * tighter registration rules said to sustain demand."""
    if not cg:
        return None
    for clause in _CLAUSE.split(sentence):
        # two named peers compared on growth: subject before the comparator, object after
        for rx, faster in ((_FASTER, True), (_SLOWER, False)):
            mm = rx.search(clause)
            if not mm:
                continue
            for g, shadow in zip(cg.groups, cg._shadow):
                t_before, t_after = clause[: mm.start()], clause[mm.end():]
                for sh in shadow:
                    t_before = re.sub(re.escape(sh), " ", t_before, flags=re.I)
                    t_after = re.sub(re.escape(sh), " ", t_after, flags=re.I)
                subj = cg._mentions(t_before, g["names"], g.get("aliases", {}))
                obj = cg._mentions(t_after, g["names"], g.get("aliases", {}))
                if len(subj) != 1 or len(obj) != 1 or subj == obj:
                    continue
                a, b = g["names"][subj[0]]["cagr"], g["names"][obj[0]]["cagr"]
                if rules.tied("cagr", a, b) and not _TIE_WORDS.search(sentence):
                    return f"'{mm.group(0)}': {subj[0]} {a:.1f}% vs {obj[0]} {b:.1f}% is a tie"
                if (a < b) if faster else (a > b):
                    return f"'{mm.group(0)}': {subj[0]} grows {a:.1f}%, {obj[0]} {b:.1f}%"
        # momentum words: every entity they describe grows faster than its parent
        mm = _MOMENTUM.search(clause)
        if mm:
            for g, hit in cg._hits(clause):
                parent = g.get("parent_cagr")
                if parent is None:
                    continue
                slow = [h for h in hit if g["names"][h]["cagr"] <= parent + 1e-9]
                if slow:
                    return (f"'{mm.group(0)}' for {slow[0]}, which grows {g['names'][slow[0]]['cagr']:.1f}%, "
                            f"not above its peer average {parent:.1f}%")
    # vendor concentration, MEA vs global
    gl, me = cg.other_share.get("global"), cg.other_share.get("mea")
    if gl is not None and me is not None and not re.search(r"\bglobal(?:ly)?\b[^.]{0,20}\bmore concentrated\b",
                                                           sentence, re.I):
        if _MORE_CONCENTRATED.search(sentence) and me >= gl:
            return f"says MEA is more concentrated, but regional/other hold {me:.1f}% in MEA vs {gl:.1f}% globally"
        if _LESS_CONCENTRATED.search(sentence) and me <= gl:
            return f"says local/regional players matter more in MEA, but they hold {me:.1f}% vs {gl:.1f}% globally"
    # a 'high X' level contradicted by an MEA restraint ('Limited digital infrastructure')
    low = sentence.lower()
    if cg.restraints and not any(re.search(rf"(?<![a-z]){re.escape(n.lower())}(?![a-z])", low) for n in cg.non_mea):
        for r in cg.restraints:
            mr = _LOW_RESTRAINT.match(r)
            if not mr:
                continue
            # a restraint scoped to a place ('Low Consumer Awareness in Africa') holds only for that place
            what, _, place = mr.group(1).lower().partition(" in ")
            if place.strip() and place.strip() not in low:
                continue
            topic = {w for w in re.findall(r"[a-z-]+", what) if w not in _TOPIC_STOP}
            for syn in _SYNONYMS:
                if topic & syn:
                    topic |= syn
            hit = next((w for w in topic if re.search(_HIGH_CLAIM + rf"(?:[\w-]+\s+){{0,2}}{re.escape(w)}\b", low)), None)
            if hit:
                return f"claims a high level of '{hit}', but the restraint '{r}' says it is low"
    if _TIGHT_RULES.search(sentence) and _DEMAND_UP.search(sentence):
        return "says tighter registration rules sustain demand (they are a barrier to entry)"
    return None


# research steps the pipeline does not perform (PLAN-07 / FINDINGS X16): never claimed in a report
FALSE_METHOD = re.compile(r"\b(expert[- ]panels?|panel of experts|expert (?:review|scoring|interviews?)|"
                          r"primary (?:interviews?|research|surveys?)|interviews? with|bottom[- ]up|"
                          r"triangulat\w*|delphi)\b", re.I)
# a demonstrative whose antecedent is not in the text ('the contribution of these factors')
_DANGLING = re.compile(r"\b(?:these|those)\s+(?:factors|trends|drivers|restraints|forces|dynamics|additives|products|"
                       r"measures|conditions|developments|shifts|challenges|pressures|solutions|tools|technologies)\b",
                       re.I)
# a global-column cell may not state MEA-basis figures ('-0.6 percentage points of MEA CAGR')
_MEA_WORDS = r"(?:MEA|Middle East|Africa|regional)"
_MEA_BASIS = re.compile(rf"\b{_MEA_WORDS}\b[^.;]{{0,40}}\b(?:CAGR|growth)\b|"
                        rf"\b(?:pp|percentage[- ]points?)\b[^.;]{{0,25}}\b{_MEA_WORDS}\b|"
                        r"(?<![\w.])[+\-−]?\d+(?:\.\d+)?\s?(?:pp\b|percentage[- ]points?)", re.I)


def drop_mea_basis(text: str, dropped: list[str]) -> str:
    """Text for a global-column slot without its MEA-basis sentences (FINDINGS X14)."""
    keep = []
    for s in sentences(text or ""):
        if _MEA_BASIS.search(s):
            dropped.append(f"[MEA-basis figure in a global slot] {s[:120]}")
        else:
            keep.append(s)
    return " ".join(keep)


_SIZE_GAP = re.compile(r"\b(gaps?|differences?|differ(?:s|ing)?|discrepanc\w*|diverg\w*|variation|var(?:y|ies)|"
                       r"spread)\b", re.I)
_SIZE_CAUSE = re.compile(r"\b(reflect\w*|because|due to|owing to|stem\w* from|explain\w*|attribut\w*|driven by|"
                         r"result\w* from|arises?|arising)\b", re.I)
_SIZE_COMPARE = re.compile(r"\b(publish\w*|third[- ]party|other (?:estimates|sources|firms|publishers|reports|"
                           r"analysts)|research firms|scope|definition\w*|methodolog\w*)\b", re.I)
_SIZE_BASE_WHY = re.compile(r"\bCMI\b[^.]{0,40}\b(?:sets?|places?|positions?|pitche?s?)\b[^.]{0,20}\b(?:base|estimate)\b"
                            r"[^.]{0,60}\b(?:because|reflect\w*|due to|given)\b", re.I)
_PUBLISHED_WORDING = re.compile(r"\bpublish\w*|\btriangulat\w*|\bthird[- ]party (?:estimate|figure|data|source)s?\b",
                                re.I)


_PUBLISHER_NAME = re.compile(
    r"\b(Precedence Research|Grand View Research|MarketsandMarkets|Markets and Markets|Mordor Intelligence|"
    r"Fortune Business Insights|IMARC|Allied Market Research|Verified Market (?:Reports|Research)|Market\.us|"
    r"Statista|Emergen Research|Market Research Future|Research and Markets|Technavio|Spherical Insights|"
    r"Data Bridge|Straits Research|Future Market Insights|Persistence Market|Transparency Market|"
    r"Polaris Market|Reanin|Global Market Insights|Expert Market Research|Cognitive Market Research|"
    r"(?!coherentmarket)\w+(?:research|marketreports|insights)\.com)\b", re.I)   # CMI's own site is allowed


def drop_size_comparison(text: str, dropped: list[str], unsourced: bool = False) -> str:
    """Drops sentences that explain a gap between the CMI figure and published
    figures ('The gap reflects scope ...'): the pipeline has no basis for such
    reasons (PLAN-02, FINDINGS E). When the headline is `unsourced` (a CMI
    estimate), every sentence with published-comparison or triangulation
    wording goes too."""
    keep = []
    for s in sentences(text or ""):
        why = ("names another research publisher" if _PUBLISHER_NAME.search(s) else
               "explains a CMI-vs-published gap" if (_SIZE_GAP.search(s) and _SIZE_CAUSE.search(s)
                                                     and _SIZE_COMPARE.search(s)) or _SIZE_BASE_WHY.search(s) else
               "published/triangulation wording on an unsourced headline" if unsourced
               and _PUBLISHED_WORDING.search(s) else None)
        if why:
            dropped.append(f"[{why}] {s[:120]}")
        else:
            keep.append(s)
    return " ".join(keep)


# --------------------------------------------------------------------------- #
# regulatory facts (PLAN-06): a regulator, law, decree, 'Vision 20xx' or dated
# policy may be named only if it is in the verified fact list (verify_facts.py)
# --------------------------------------------------------------------------- #
_CAP = r"[A-Z][\w&'’-]*"
_BODY = (r"Ministry|Authority|Agency|Directorate|Inspectorate|Commission|Administration|Regulator|Bureau|Board|"
         r"Council|Department")
_TAIL = rf"(?:(?:\s+|,\s+)(?:(?:of|for|and|on|the|&)\s+)*{_CAP})*"
_REG_BODY = re.compile(rf"(?:{_CAP}\s+){{0,4}}(?:{_BODY})\b{_TAIL}")
_REG_LAW = re.compile(rf"(?:{_CAP}\s+){{1,6}}(?:Act|Law|Decree|Directive|Regulation|Ordinance|Charter|Strategy|"
                      rf"Plan|Programme|Program|Policy|Framework|Guidelines)\b(?:\s+(?:No\.?\s*)?\d[\d/]*)?"
                      rf"(?:\s+of\s+(?:19|20)\d\d)?|\b(?:Act|Law|Decree)\s+(?:No\.?\s*)?\d[\d/]*(?:\s+of\s+(?:19|20)\d\d)?|"
                      rf"\b(?:Regulation|Directive)\s+(?:\((?:EU|EC|EEC)\)\s+)?(?:No\.?\s*)?\d{{2,4}}/\d+(?:/\w+)?")
_VISION = re.compile(r"\bVision\s+20\d\d\b")
_DATED_POLICY = re.compile(r"(?<!\bby )(?<!\bto )(?<!\buntil )(?<!\bthrough )(?<!\btowards )(?<!\bbefore )"
                           r"(?<![\w-])((?:19|20)\d\d)(?![\w-])[^.;,]{0,40}?\b(act|law|decree|directive|regulation|ban|"
                           r"policy|plan|strategy|framework|guidelines?|charter|amendment|revision|reform|mandate)\b|"
                           r"\b(act|law|decree|directive|regulation|ban|policy|plan|strategy|framework|guidelines?|"
                           r"charter|amendment|revision|reform|mandate)\b[^.;,]{0,30}?\b(?:of|in|from|since)\s+"
                           r"((?:19|20)\d\d)(?![\w-])", re.I)
_REG_CONTEXT = re.compile(r"\b(regulat\w*|approv\w*|regist\w*|licen[cs]\w*|authori[sz]\w*|law|act|decree|ban\w*|"
                          r"polic\w*|complian\w*|enforce\w*|permit\w*|ministry|agency|mandat\w*)\b", re.I)
# acronyms that are never a regulator
_NOT_REGULATOR = {"CMI", "CAGR", "MEA", "GCC", "USD", "UAE", "USA", "KSA", "GDP", "ASEAN", "APAC", "LATAM", "EMEA",
                  "GMP", "HACCP", "ISO", "ESG", "IOT", "RAS", "SME", "SMES", "B2B", "B2C", "OEM", "OEMS", "API", "SAAS",
                  "NGO", "NGOS", "FDI", "R&D", "LLM", "LLMS", "GENAI", "AI", "MENA", "SSA", "ROI", "KPI", "KPIS",
                  "COVID", "PPP", "EU", "UK", "US"}
_NOT_BODY = ("gulf cooperation council",)
_GENERIC = {"ministry", "authority", "agency", "directorate", "inspectorate", "commission", "administration",
            "regulator", "bureau", "board", "council", "department", "act", "law", "decree", "directive",
            "regulation", "ordinance", "charter", "strategy", "plan", "programme", "program", "policy", "framework",
            "guidelines", "guideline", "national", "general", "federal", "the", "and", "for"}


def _fact_words(s: str) -> set[str]:
    return {w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w
            for w in re.findall(r"[a-z]+", s.lower()) if len(w) >= 3}


def _named_in(phrase: str, facts: list[str]) -> bool:
    """`phrase` (a body or instrument name) is one of the verified facts: verbatim,
    or most of its distinctive words in one fact."""
    low = re.sub(r"\s+", " ", phrase.lower()).strip()
    if any(low in f.lower() for f in facts):
        return True
    w = _fact_words(phrase) - _GENERIC
    if not w:
        return True                      # nothing distinctive ('the Ministry') -- not a named fact
    return any(len(w & _fact_words(f)) / len(w) >= 0.8 for f in facts)


def unverified_fact(sentence: str, facts: list[str] | None) -> str | None:
    """Why `sentence` names a regulatory fact that is not in `facts` (the
    verified fact list), or None. Checked: government bodies ('Ministry of
    ...', '... Authority'), acronyms in a regulatory sentence ('SFDA'), named
    laws and instruments ('Fertilizers Act', 'Act 36 of 1947', 'Regulation (EU)
    2019/1009'), 'Vision 20xx' and year-dated policies ('the 2023 Animal Feed
    Act revision'). `facts` None means no verification ran: nothing is checked."""
    if facts is None or not sentence:
        return None
    if re.search(r"\[(?:to verify|unverified)\]", sentence, re.I):
        return "carries a verification marker"
    for m in _VISION.finditer(sentence):
        if not any(m.group(0).lower() in f.lower() for f in facts):
            return f"names '{m.group(0)}', which is not a verified fact"
    for m in _REG_BODY.finditer(sentence):
        # a possessive left of the body names its country, not the body ("Egypt's Ministry of ...")
        name = re.split(r"\b\w+['’]s\s+", m.group(0))[-1].strip(" ,")
        if any(n in name.lower() for n in _NOT_BODY):
            continue
        if not _named_in(name, facts):
            return f"names '{name}', which is not a verified fact"
    for m in _REG_LAW.finditer(sentence):
        name = re.split(r"\b\w+['’]s\s+", m.group(0))[-1].strip(" ,")
        if not _named_in(name, facts):
            return f"names '{name}', which is not a verified fact"
    if _REG_CONTEXT.search(sentence):
        # a hyphenated acronym is a company or a modifier ('DSM-Firmenich', 'FAO-guided'), not a regulator
        for a in re.findall(r"(?<![\w&-])[A-Z][A-Z&]{2,7}(?![\w&-])", sentence):
            if a.upper() not in _NOT_REGULATOR and not any(re.search(rf"\b{re.escape(a)}\b", f) for f in facts):
                return f"names '{a}', which is not a verified fact"
    for m in _DATED_POLICY.finditer(sentence):
        year = m.group(1) or m.group(4)
        noun = (m.group(2) or m.group(3)).lower()
        ctx = _fact_words(m.group(0)) - _GENERIC - {noun}
        if not any(year in f and (not ctx or ctx & _fact_words(f)) for f in facts):
            return f"names a {year}-dated {noun}, which is not a verified fact"
    return None


# --------------------------------------------------------------------------- #
# scope (PLAN-04): text may not describe the market at another value-chain level,
# or count something the definition excludes
# --------------------------------------------------------------------------- #
_SCOPE_STOP = {"and", "or", "the", "of", "for", "with", "without", "that", "only", "a", "an", "other", "others",
               "products", "product", "systems", "system", "market", "markets", "all", "any", "its", "their",
               "from", "into", "such", "as", "e.g", "eg", "etc", "not", "non", "are", "is", "be", "by", "in",
               "on", "to", "at", "where", "which", "used", "use", "uses", "type", "types", "based", "global",
               "application", "applications", "grade", "grades", "segment", "segments", "sales", "revenue",
               "revenues", "industry", "industries", "sector", "sectors", "related"}
_EQUIP = (r"equipment|aerat\w*|machinery|hardware|sensors?|monitoring (?:systems?|equipment|devices?|solutions?)|"
          r"automation (?:systems?|equipment|solutions?)|technolog\w*|software|platforms?")
_REVENUE = r"sales|revenues?|spending|spend|purchases|market|vendors?|suppliers?|makers?|manufacturers?"
_EQUIP_REVENUE = re.compile(rf"\b(?:{_EQUIP})\s+(?:{_REVENUE})\b|"
                            rf"\b(?:sales|revenues?|spending|spend|purchases)\s+(?:of|on|from|for)\s+"
                            rf"(?:[\w-]+\s+){{0,3}}?(?:{_EQUIP})\b", re.I)
_OUTPUT_VALUE = re.compile(r"\b(?:farm[- ]gate|ex[- ]farm|harvest|crop|output|produce)\s+(?:value|revenues?)\b|"
                           r"\bvalue of (?:the )?(?:crops?|harvests?|output|produce|shrimp|livestock)\b", re.I)
_RETAIL_VALUE = re.compile(r"\bretail (?:sales|value|revenues?|prices?)\b|\bshelf prices?\b", re.I)
_UPSTREAM_VALUE = re.compile(r"\b(?:ex[- ]factory|farm[- ]gate|wholesale) (?:value|sales|prices?)\b", re.I)
# a sentence that sizes / counts something (vs one that only mentions it)
_SIZING = {
    "farm-gate output": [(_EQUIP_REVENUE, "equipment/technology sales"), (_RETAIL_VALUE, "retail value")],
    "producer": [(_EQUIP_REVENUE, "equipment/technology sales"), (_RETAIL_VALUE, "retail value")],
    "manufacturer": [(_EQUIP_REVENUE, "equipment/technology sales"), (_RETAIL_VALUE, "retail value")],
    "service": [(_EQUIP_REVENUE, "equipment/technology sales")],
    "vendor/software": [(_OUTPUT_VALUE, "the value of what buyers produce")],
    "retail": [(_UPSTREAM_VALUE, "ex-factory/farm-gate value")],
}
# a heat-map / segment row that IS equipment or technology (an output market has none)
_EQUIP_ROW = re.compile(r"\b(?:equipment|aerat\w*|machinery|hardware|sensors?|monitoring|automation|technolog\w*|"
                        r"software|platforms?|devices?)\b", re.I)
_ROW_RULES = {"farm-gate output": (_EQUIP_ROW, "an equipment/technology row"),
              "producer": (_EQUIP_ROW, "an equipment/technology row")}
# wording that puts something inside the market ('X accounts for 12% of the market'), not just near it
_COUNTS = re.compile(r"\b(?:part of (?:the |this )?market|of (?:the |this )?market(?:'s)? (?:value|revenue|size)|"
                     r"market share|(?:largest|smallest|fastest|key|main) segment|segment of|accounts? for|counted|"
                     r"included in|share of (?:the |this )?(?:market|revenue|value)|revenues? from|sales of)\b", re.I)
NEGATED = re.compile(r"\b(exclud\w*|outside|without|not(?:counted|included|part|covered)|out of scope|beyond|rather than|"
                      r"other than|instead of|unlike|apart from|except)\b", re.I)


def _stem(w: str) -> str:
    w = w.lower()
    return (w[:-1] if len(w) > 3 and w.endswith("s") else w)[:6]


def _word(w: str) -> str:
    """A whole word in its singular form ('hatcheries' -> 'hatchery'; 'processors' stays apart from 'processing')."""
    w = w.lower()
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def scope_stems(text: str, ignore: set[str] = frozenset(), whole: bool = False) -> set[str]:
    """Content-word stems of a phrase ('Animal Feed & Pet Food' -> {'animal', 'feed', 'pet', 'food'});
    `whole` keeps whole (singular) words instead of 6-letter stems."""
    text = re.sub(r"\((?:e\.g\.?|i\.e\.?|such as|incl\w*)[^)]*\)", " ", text or "", flags=re.I)
    f = _word if whole else _stem
    return {f(w) for w in re.findall(r"[a-z]+", text.lower()) if len(w) >= 3 and w not in _SCOPE_STOP} - ignore


def market_stems(definition: dict | None, whole: bool = False) -> set[str]:
    """The market's own words (every sentence shares them, so they never mark scope)."""
    return scope_stems(re.sub(r"^\s*global\s+|\s+market\s*$", "", str((definition or {}).get("market_name") or ""),
                              flags=re.I), whole=whole)


def excluded_items(definition: dict | None) -> list[str]:
    """The excluded items as short phrases: the list, or an older text split at , ; /."""
    v = (definition or {}).get("excluded_items") or (definition or {}).get("excluded") or []
    if isinstance(v, str):
        v = re.split(r"[,;/]|\band\b", re.sub(r"\((?:e\.g\.?|i\.e\.?)[^)]*\)", " ", v))
    return [x.strip(" .") for x in v if isinstance(x, str) and x.strip(" .")]


def excluded_hit(text: str, definition: dict | None, min_words: int = 1) -> str | None:
    """The excluded item `text` names (all of the item's own words present as
    whole words, and the item at least `min_words` words long), or None.
    Whole words, because 'processors' (a buyer) is not 'processing' (an activity)."""
    own = market_stems(definition, whole=True)
    words = scope_stems(text, own, whole=True)
    for item in excluded_items(definition):
        need = scope_stems(item, own, whole=True)
        if need and min_words <= len(need) <= 4 and need <= words:
            return item
    return None


def scope_violation(sentence: str, definition: dict | None, *, row: bool = False) -> str | None:
    """Why `sentence` describes the market outside its definition, or None.
    Prose fails when it sizes the market at another value-chain level
    ('equipment sales' in a farm-gate output market) or counts an excluded
    item as part of it. `row=True` is for heat-map / use-case / segment rows,
    which are part of the market by construction: naming an excluded item, or
    (for an output market) being an equipment/technology row, is enough."""
    if not definition or not sentence:
        return None
    level = str(definition.get("value_chain_level") or "").lower()
    if row:
        rule = _ROW_RULES.get(level)
        if rule and rule[0].search(sentence):
            return f"{rule[1]} in a {level} market"
        hit = excluded_hit(sentence, definition)
        return f"names '{hit}', which the definition excludes" if hit else None
    for pat, what in _SIZING.get(level, []):
        m = pat.search(sentence)
        if m and not NEGATED.search(sentence):
            return f"describes {what} ('{m.group(0)}') in a {level} market"
    hit = excluded_hit(sentence, definition)
    if hit and _COUNTS.search(sentence) and not NEGATED.search(sentence):
        return f"counts '{hit}', which the definition excludes"
    return None


# singular verb -> plural, for a plural segment name used as a subject ('Microbial Biostimulants remains')
_PLURAL_VERB = {"is": "are", "was": "were", "has": "have", "remains": "remain", "grows": "grow", "accounts": "account",
                "leads": "lead", "holds": "hold", "represents": "represent", "captures": "capture",
                "dominates": "dominate", "makes": "make", "continues": "continue", "shows": "show", "rises": "rise",
                "expands": "expand", "contributes": "contribute", "reaches": "reach", "posts": "post",
                "stays": "stay", "follows": "follow", "trails": "trail", "adds": "add", "benefits": "benefit",
                "commands": "command", "outpaces": "outpace", "forms": "form", "ranks": "rank", "lags": "lag"}


def is_plural_name(name: str) -> bool:
    """'Microbial Biostimulants', 'Cereals & Grains' are plural; 'Hydroponics', 'Glass', 'Asparagus' are not."""
    last = re.sub(r"\s*\([^)]*\)\s*$", "", name).split()[-1].lower() if name.strip() else ""
    return len(last) > 3 and last.endswith("s") and not last.endswith(("ss", "us", "is", "ics", "ous"))


def fix_plurals(text: str, names) -> str:
    """Subject-verb agreement for plural segment names ('Microbial Biostimulants remain')."""
    for n in sorted((n for n in names or [] if is_plural_name(n)), key=len, reverse=True):
        text = re.sub(rf"(?<![\w-]){re.escape(n)}(?=\s+(?:{'|'.join(_PLURAL_VERB)})\b)\s+(\w+)",
                      lambda mm: f"{n} {_PLURAL_VERB.get(mm.group(1).lower(), mm.group(1))}", text)
    return text


# Attribution tags on every figure ('$31.1M, CMI estimates.', '(CMI estimate)', 'CMI assessments') read as
# hedging: the report's figures are CMI's by default (user decision 2026-10-16). Only the headline sentence
# 'CMI estimates the market at ...' keeps the wording -- it is the subject and verb, not a tag.
_TAG_SUBS = [
    (re.compile(r"\s*\((?:a\s+)?CMI(?:'s)?\s+(?:estimates?|assessments?|forecasts?)\)", re.I), ""),
    (re.compile(r",?\s*(?:on|by|per|in)?\s*CMI(?:'s)?\s+(?:estimates?|assessments?|forecasts?)(?=\s*[.;,)]|\s*$)",
                re.I), ""),
    (re.compile(r"(?<=[,;(])\s*CMI\s+(?:estimates|forecasts)\s+(?=[+$\d])"), " at "),
    (re.compile(r"\b(?:is|are)\s+CMI-(?:estimated|forecast)\s+at\b", re.I), lambda mm: mm.group(0).split()[0] + " valued at"),
    (re.compile(r"\bCMI-(estimated|forecast)\b", re.I), r"\1"),
    (re.compile(r"\s*,\s*,"), ","),
]
_TAG_OPENER = re.compile(r"^CMI(?:'s)?\s+(?:estimates|assessments|forecasts|analysis)\s+(?:shows?\s+|indicates?\s+)?that\s+",
                         re.I)
# a sentence that only comments on where a figure comes from ('The headline is a CMI estimate.')
_TAG_ONLY = re.compile(r"^(?:The|This|These|All|Its)\s+(?:headline|figures?|estimates?|values?|numbers?)\s+"
                       r"(?:is|are)\s+(?:an?\s+)?CMI(?:'s)?\s+(?:own\s+)?(?:estimates?|forecasts?|assessments?)\W*$", re.I)


def strip_tags(s: str) -> str | None:
    """One sentence without its attribution tags; None for a sentence that is only a tag."""
    out = re.sub(r"\s*\[CHART\]\s*", " ", s).strip()      # the prompt's slot marker, copied by the model
    if _TAG_ONLY.match(out):
        return None
    for rx, rep in _TAG_SUBS:
        out = rx.sub(rep, out)
    mm = _TAG_OPENER.match(out)
    if mm:
        rest = out[mm.end():]
        out = rest[:1].upper() + rest[1:]
    out = re.sub(r"\s+([.,;])", r"\1", out).strip()
    return out or None


def clean_text(text: str, nguard: NumberGuard, cguard: ClaimGuard | None, dropped: list[str],
               absent: list[str] | None = None) -> str:
    """The text with every failing sentence removed. `absent`: entity names
    that do not exist in this report's data (e.g. 'Rest of North America'
    when the table has no such row) -- a sentence naming one is dropped.
    A sentence that leans on a removed one ('This ...', 'Together they ...')
    is removed with it, and a text may not open with such a pronoun. A
    sentence outside the market definition (`cguard.definition`, see
    scope_violation) is dropped too."""
    keep = []
    prev_dropped = True                     # nothing precedes the first sentence
    definition = getattr(cguard, "definition", None)
    for s in sentences(text):
        s = strip_tags(s)
        if not s:
            continue
        bad = nguard.bad(s)
        gone = [a for a in absent or [] if a.lower() in s.lower()]
        scope = scope_violation(s, definition) if definition else None
        why = (f"numbers not in model: {bad}" if bad else
               f"names {gone}, which is not in the data" if gone else
               f"out of scope: {scope}" if scope else
               "template/draft caveat" if _BANNED.search(s) else
               "drone-template phrasing" if _TEMPLATE_PHRASES.search(s) else
               f"claims a research step the pipeline does not perform ('{FALSE_METHOD.search(s).group(0)}')"
               if FALSE_METHOD.search(s) else
               "pronoun refers to removed or missing text" if prev_dropped and _ORPHAN.match(s) else
               # a quoted finding has lost its context: 'these factors' there points at nothing
               "refers to factors that are not in the text" if s.startswith("Finding:") and _DANGLING.search(s) else
               "copied from the template's example text" if cguard and copied(s, cguard.template_sentences,
                                                                                 cguard.name_words) else
               (cguard.problem(s) or cguard.ordinal(s) or cguard.growth_vs(s, cguard.global_cagr) or
                cguard.qualified(s) or
                (f"contradicts the data: {c}" if (c := contradiction_guard(s, cguard)) else None) or
                (no_breakdown_claim(s, cguard.no_breakdown, cguard.segment_names)
                 if cguard.no_breakdown else None)) if cguard else None)
        prev_dropped = bool(why)
        if why:
            dropped.append(f"[{why}] {s[:120]}")
        else:
            keep.append(s)
    return " ".join(keep)


# --------------------------------------------------------------------------- #
# vendors (PLAN-05): text names only the final vendor data, with its own shares,
# and a "recent" deal carries a year from the evidence
# --------------------------------------------------------------------------- #
_VENDOR_PCT = re.compile(r"(?<![\d.])\d+(?:\.\d+)?%")
_DEAL = re.compile(r"\b(deals?|acqui\w*|merg\w*|partner\w*|joint ventures?|invest\w*|expan\w*|launch\w*|open\w*|"
                   r"agreements?|stakes?|moves?|entered|entry)\b", re.I)
_RECENT = re.compile(r"\s*\b(?:more\s+)?recent(?:ly)?\b\s*", re.I)
_YEAR = re.compile(r"(?<!\d)(20[0-4]\d)(?!\d)")


def vendor_problem(sentence: str, vt: dict | None) -> str | None:
    """Why a sentence breaks the vendor data, or None. `vt` is m["vendor_text"]:
    {"names", "blocked", "shares": {vendor: [printed %]}, "moves": {vendor: [years]}, "year"}.
    - a company that is not in the final vendor data (excluded, unconfirmed,
      merged away) may not be named;
    - a % in a sentence naming a vendor must be the printed share of a vendor
      it names, and a vendor share may only carry the vendor year;
    - a "recent" deal or move needs a year confirmed by the evidence."""
    if not vt or not sentence:
        return None
    from AiAgentsFleet.cmi_report.entities import named_in
    # company names are capitalised in prose; 'Egyptian shrimp farms' is not 'Egyptian Shrimp Company'
    named = [n for n in vt.get("names") or [] if named_in(sentence, n, case=True)]
    blocked = [n for n in vt.get("blocked") or [] if named_in(sentence, n, case=True)
               and not any(named_in(n, x) or named_in(x, n) for x in named)]
    if blocked:
        return f"names {blocked[0]}, which is not in the final vendor data"
    pcts = _VENDOR_PCT.findall(sentence)
    if named and pcts:
        shares = vt.get("shares") or {}
        allowed = {p for n in named for p in (shares.get(n) or [])}
        bad = [p for p in pcts if p not in allowed]
        if bad:
            return f"vendor share {bad[0]} is not in the vendor data"
        years = {int(y) for y in _YEAR.findall(sentence)}
        if years - {vt.get("year")}:
            return f"vendor share stated for {sorted(years - {vt.get('year')})[0]}, not {vt.get('year')}"
    if _RECENT.search(sentence) and _DEAL.search(sentence):
        years = {int(y) for y in _YEAR.findall(sentence)}
        known = {y for n in named for y in (vt.get("moves") or {}).get(n, [])}
        if not years:
            return "undated 'recent' deal or move"
        if not years <= known:
            return f"deal year {sorted(years - known)[0]} not confirmed by the evidence"
    return None


def vendor_clean(text: str, vt: dict | None, dropped: list[str]) -> str:
    """The text with every clause that breaks the vendor data removed (a "recent"
    claim is judged clause by clause, so 'Present in Egypt; recent moves include
    ...' keeps its first half), and "recent" dropped from a dated, confirmed deal."""
    if not vt or not text:
        return text or ""
    keep = []
    for s in sentences(text):
        parts = [p for p in re.split(r"\s*;\s*", s) if p.strip()]
        ok = []
        for p in parts:
            why = vendor_problem(p, vt)
            if why:
                dropped.append(f"[vendor: {why}] {p[:120]}")
            else:
                ok.append(re.sub(r"^[\W_]+", "", _RECENT.sub(" ", p)).strip() if _DEAL.search(p) else p)
        if ok:
            out = "; ".join(ok)
            out = out[0].upper() + out[1:]
            keep.append(out if out[-1] in ".!?" else out + ".")
    return " ".join(keep)
