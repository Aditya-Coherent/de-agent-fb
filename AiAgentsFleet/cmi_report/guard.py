"""Sentence-level checks that keep generated text faithful to the model:

  * NumberGuard -- every number in a sentence must be one of the model's
    facts (as formatted by fmt.py, any rounding of it) or a year / small count;
  * ClaimGuard  -- a sentence that calls something the largest / fastest /
    smallest / slowest / biggest opportunity must be naming the entity that
    really is, within that entity's own peer group.

A sentence that fails is dropped (not the whole paragraph).
"""
from __future__ import annotations

import re

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
                     r"U\.A\.E|S\.A|N\.V|B\.V|A\.G|S\.p\.A|Pty|Pvt|Bhd|Tbk|etc|cf|al)\.$")


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
_QUALIFIED = re.compile(r"\b(second|third|fourth|fifth|one of|among|next|after)\b[\w\s-]{0,15}$", re.I)
_CLAUSE = re.compile(r";|,\s*(?:but|while|whereas|and)\b|\bwhile\b|\bwhereas\b|\bbut\b")


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
        tie_words = re.search(r"\b(tie|tied|joint|equal|level with|alongside)\b", sentence, re.I)
        for clause in _CLAUSE.split(sentence):
            for kind, pat in _SUPER.items():
                m = re.search(pat, clause, re.I)
                if not m or _QUALIFIED.search(clause[: m.start()]):
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
                        return f"'{m.group()}' names {hit} but it is {order[0]} ({key})"
                    # a tie at printed precision must be described as a tie
                    if len(order) > 1 and not tie_words and \
                            self._printed(key, d[order[0]][key]) == self._printed(key, d[order[1]][key]):
                        return f"'{m.group()}' for {order[0]}, but it is tied with {order[1]}"
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


def drop_size_comparison(text: str, dropped: list[str], unsourced: bool = False) -> str:
    """Drops sentences that explain a gap between the CMI figure and published
    figures ('The gap reflects scope ...'): the pipeline has no basis for such
    reasons (PLAN-02, FINDINGS E). When the headline is `unsourced` (a CMI
    estimate), every sentence with published-comparison or triangulation
    wording goes too."""
    keep = []
    for s in sentences(text or ""):
        why = ("explains a CMI-vs-published gap" if (_SIZE_GAP.search(s) and _SIZE_CAUSE.search(s)
                                                     and _SIZE_COMPARE.search(s)) or _SIZE_BASE_WHY.search(s) else
               "published/triangulation wording on an unsourced headline" if unsourced
               and _PUBLISHED_WORDING.search(s) else None)
        if why:
            dropped.append(f"[{why}] {s[:120]}")
        else:
            keep.append(s)
    return " ".join(keep)


def clean_text(text: str, nguard: NumberGuard, cguard: ClaimGuard | None, dropped: list[str],
               absent: list[str] | None = None) -> str:
    """The text with every failing sentence removed. `absent`: entity names
    that do not exist in this report's data (e.g. 'Rest of North America'
    when the table has no such row) -- a sentence naming one is dropped.
    A sentence that leans on a removed one ('This ...', 'Together they ...')
    is removed with it, and a text may not open with such a pronoun."""
    keep = []
    prev_dropped = True                     # nothing precedes the first sentence
    for s in sentences(text):
        bad = nguard.bad(s)
        gone = [a for a in absent or [] if a.lower() in s.lower()]
        why = (f"numbers not in model: {bad}" if bad else
               f"names {gone}, which is not in the data" if gone else
               "template/draft caveat" if _BANNED.search(s) else
               "drone-template phrasing" if _TEMPLATE_PHRASES.search(s) else
               "pronoun refers to removed or missing text" if prev_dropped and _ORPHAN.match(s) else
               "copied from the template's example text" if cguard and copied(s, cguard.template_sentences,
                                                                                 cguard.name_words) else
               (cguard.problem(s) or cguard.growth_vs(s, cguard.global_cagr) or cguard.qualified(s) or
                (no_breakdown_claim(s, cguard.no_breakdown, cguard.segment_names)
                 if cguard.no_breakdown else None)) if cguard else None)
        prev_dropped = bool(why)
        if why:
            dropped.append(f"[{why}] {s[:120]}")
        else:
            keep.append(s)
    return " ".join(keep)
