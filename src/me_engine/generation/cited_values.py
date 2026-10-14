"""Cited market-size figures for Desk Research: extract, normalise, scope-check, combine.

The market-size node used to rely on the LLM to spot (and copy correctly) a
cited figure in the evidence brief. In the five-industry example runs only
about 3 of 7 markets ended up grounded, even when the snippets held a usable
number (Televisions: 20 web results, "no cited figure found"). This module
does that step in code:

1. `extract_values` pulls every "USD 245.3 billion" / "$64.2 Bn" /
   "US$ 13,590 million" figure out of a text, converts it to US$ Mn and finds
   the year stated next to it.
2. `check_scope` rejects a figure whose sentence is about a different scope
   than the market being sized ("surgical gloves" for all medical gloves,
   "urea fertilizer" for total urea, a single region for a global market).
   It is a keyword check; with a written market definition an LLM check
   decides instead (`llm_scope_verdicts`).
3. `collect_cited_values` keeps one 2024-2026 figure per source, moves it to
   2025 at the source's CAGR, rejects figures far outside an independent
   units x ASP range, anchors on research-report sources (tier 1), drops
   outliers more than 2.5x from the median and returns the median of what is
   left -- or, when that consensus is weak, the value nearest the size range.

Nothing here calls the network: callers pass the texts in.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from statistics import median
from typing import Any
from urllib.parse import urlparse

from .db_source import geo_qualifiers

UNIT_TO_MN = {
    "trillion": 1_000_000, "tn": 1_000_000,
    "billion": 1_000, "bn": 1_000, "b": 1_000,
    "million": 1, "mn": 1, "mm": 1, "m": 1,
}
_UNIT = r"(trillion|tn|billion|bn|b|million|mn|mm|m)"
_NUMBER = r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
# "USD 245.3 billion", "US$ 13,590 million", "$64.2 Bn", "$64.2B"
_VALUE_RE = re.compile(rf"(?<![\w.])(?:US\$|USD|US\s?dollars?|\$)\s*{_NUMBER}\s*{_UNIT}(?![a-z])", re.IGNORECASE)
# "64.2 billion USD"
_VALUE_AFTER_RE = re.compile(rf"(?<![\w.$]){_NUMBER}\s*{_UNIT}\s*(?:USD|US\$|U\.S\.\s?dollars)(?![a-z])", re.IGNORECASE)

CAGR_RE = re.compile(
    r"CAGR[^0-9%]{0,40}?(\d{1,2}(?:\.\d+)?)\s*%|(\d{1,2}(?:\.\d+)?)\s*%\s*CAGR", re.IGNORECASE
)

ACCEPTED_YEARS = (2024, 2025, 2026)
TARGET_YEAR = 2025
DEFAULT_CAGR = 0.05
OUTLIER_FACTOR = 2.5

# Year stated right after the value: "USD 63.9 billion in 2024", "... by 2033".
_YEAR_AFTER_RE = re.compile(
    r"^[^.;:|]{0,25}?\b(in|for|as of|during|by|to|through|until)\s+(?:the\s+(?:year|end of)\s+)?(20\d\d)\b(?!\s*[-–]\s*20\d\d)",
    re.IGNORECASE,
)
_YEAR_PAREN_RE = re.compile(r"^\s*\((?:in\s+)?(20\d\d)\)")
# Year stated before the value: "In 2025, the market was valued at", "2025 market size:".
_YEAR_BEFORE_RES = [
    re.compile(r"\b(?:in|for|as of|during)\s+(20\d\d)\b(?!\s*[-–]\s*20\d\d)[^.;]*$", re.IGNORECASE),
    re.compile(r"(?<![-–]\s)(?<![-–])\b(20\d\d)\s+(?:global\s+)?(?:market\s+)?(?:size|value|valuation|revenue)\b[^.;]{0,40}$", re.IGNORECASE),
    re.compile(r"(?<![-–])(?<![-–]\s)\b(20\d\d)\s*[:=,]?\s*$"),
]
_FORECAST_PREPS = {"by", "to", "through", "until"}
# "grow by USD 73.1 billion", "an increase of $5 Bn": a change, not a size.
_INCREMENT_RE = re.compile(r"\b(?:grow(?:th)?|increase|increment(?:al)?|rise|add)\w*\s+(?:by|of)\s*$", re.IGNORECASE)
# A sentence about a part of the market rather than its total.
_PART_RE = re.compile(
    r"\b(segment|sub-segment|share|accounted|accounting|held|holding|region|regional|country|category|"
    r"application|end[- ]users?|end-use|type|component)s?\b",
    re.IGNORECASE,
)

# --- scope ------------------------------------------------------------------ #
_SUBJECT_RE = re.compile(r"\b(market|industry)s?\b", re.IGNORECASE)
# Words skipped (not part of the scope) when walking back from "market".
_GENERIC = {
    "global", "worldwide", "world", "total", "overall", "international", "entire", "whole",
    "current", "annual", "estimated", "projected", "expected", "forecast", "market", "markets", "industry",
}
# Words that end the subject phrase when walking back from "market".
_BOUNDARY = {
    "the", "a", "an", "of", "to", "at", "by", "for", "in", "on", "as", "is", "was", "were", "be",
    "been", "that", "which", "and", "or", "with", "from", "our", "its", "their", "this", "these",
    "report", "reports", "according", "said", "says", "valued", "reached", "size", "share",
    "research", "insights", "analysis", "study", "data", "while", "whereas", "however", "than",
}
_SYNONYMS = {"tv": "television", "tvs": "television", "wristwatch": "watch", "handset": "smartphone"}
_MARKET_NAME_NOISE = {"global", "market", "the", "industry"}


def _singular(word: str) -> str:
    if len(word) <= 3 or word.endswith("ss"):
        return word
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith(("ches", "shes", "xes", "sses", "zes")):
        return word[:-2]
    if word.endswith("s"):
        return word[:-1]
    return word


def _norm(word: str) -> str:
    w = word.lower().strip("'’")
    w = _SYNONYMS.get(w, w)
    return _SYNONYMS.get(_singular(w), _singular(w))


def market_tokens(market_name: str) -> list[str]:
    """Core scope words of a market name, normalised: 'Disposable Medical
    Gloves' -> ['disposable', 'medical', 'glove']."""
    words = re.findall(r"[a-z0-9]+", (market_name or "").lower())
    return [_norm(w) for w in words if w not in _MARKET_NAME_NOISE]


def _merge_compounds(tokens: list[str], wanted: set[str]) -> list[str]:
    """'smart' 'watch' -> 'smartwatch' when the market name spells it as one word."""
    out: list[str] = []
    i = 0
    while i < len(tokens):
        if i + 1 < len(tokens) and _norm(tokens[i] + tokens[i + 1]) in wanted:
            out.append(_norm(tokens[i] + tokens[i + 1]))
            i += 2
        else:
            out.append(tokens[i])
            i += 1
    return out


def _sentence_bounds(text: str, pos: int) -> tuple[int, int]:
    """Start/end of the sentence (or title fragment) containing `pos`."""
    breaks = [m.end() for m in re.finditer(r"[.!?]\s+(?=[A-Z])|\s\|\s|\n|\.\.\.|…", text)]
    start = max([b for b in breaks if b <= pos], default=0)
    end = min([b for b in breaks if b > pos], default=len(text))
    return start, end


def subject_phrase(sentence_before_value: str) -> str:
    """The words naming the market just before the last 'market' / 'industry'
    ('The global TV market was valued at' -> 'TV'). Empty when the sentence
    never names a market before the value."""
    matches = list(_SUBJECT_RE.finditer(sentence_before_value))
    if not matches:
        return ""
    head = sentence_before_value[: matches[-1].start()]
    # "...by 2030.Cloud Computing market" -- a sentence break with no space.
    head = re.sub(r"(?<=[a-z0-9])\.(?=[A-Z])", ". ", head)
    words: list[str] = []
    for tok in reversed(re.findall(r"[A-Za-z0-9][\w&'’.-]*|[^\sA-Za-z0-9]", head)):
        low = tok.lower().rstrip(".")
        if not re.match(r"[a-z0-9]", low) or low in _BOUNDARY or re.fullmatch(r"20\d\d", low):
            break
        if low in _GENERIC:
            continue
        words.append(tok.rstrip("."))
        if len(words) >= 6:
            break
    return " ".join(reversed(words))


@dataclass
class ScopeVerdict:
    ok: bool
    reason: str


def check_scope(subject: str, market_name: str, *, keyword_rules: bool = True) -> ScopeVerdict:
    """Keyword scope check of a cited figure's subject phrase against the market.

    Rejects (a) no market named before the value, (b) a geography the market
    name doesn't have, and -- when `keyword_rules` -- (c) extra qualifiers that
    narrow or change the scope ('surgical gloves' for medical gloves) and
    (d) a subject that drops the market's head noun or most of its words
    ('urea' for urea fertilizer, i.e. broader). `keyword_rules=False` leaves
    (c)/(d) to an LLM check against a written market definition.
    """
    if not subject.strip():
        return ScopeVerdict(False, "no market named next to the value")
    extra_geo = geo_qualifiers(subject) - geo_qualifiers(market_name)
    if extra_geo:
        return ScopeVerdict(False, f"regional figure ({', '.join(sorted(extra_geo))}), not global")
    if not keyword_rules:
        return ScopeVerdict(True, "subject names a market; scope judged against the definition")
    want = market_tokens(market_name)
    want_set = set(want)
    raw = [_norm(w) for w in re.findall(r"[a-z0-9]+", subject.lower())]
    got = [t for t in _merge_compounds(raw, want_set) if t not in _GENERIC]
    got_set = set(got)
    extra = [t for t in got if t not in want_set]
    if extra:
        return ScopeVerdict(False, f"different/narrower scope: '{subject}' adds {extra}")
    missing = [t for t in want if t not in got_set]
    if want and want[-1] in missing:
        return ScopeVerdict(False, f"broader/different scope: '{subject}' lacks '{want[-1]}'")
    if len(missing) * 2 > len(want):
        return ScopeVerdict(False, f"broader scope: '{subject}' lacks {missing}")
    return ScopeVerdict(True, "subject matches market" + (f" (without {missing})" if missing else ""))


# --- extraction --------------------------------------------------------------- #
@dataclass
class CitedValue:
    source: str              # "CMI report DB: <keyword>" or a URL / domain
    raw_text: str            # the matched figure, e.g. "USD 63.9 billion"
    value_mn: float          # as stated, in US$ Mn
    year: int | None         # year stated next to the value
    sentence: str            # the sentence it came from (for audit)
    subject: str = ""        # market named before the value
    value_2025_mn: float | None = None
    cagr_used: float | None = None
    cagr_basis: str = ""     # "cited" | "evidence median" | "default"
    status: str = "candidate"  # accepted | corroborating | rejected
    reason: str = ""
    subject_basis: str = "sentence"  # sentence | page title | url slug
    tier: int | None = None    # 1 = research report page / CMI DB, 2 = other sites

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _year_for(text: str, start: int, end: int, s_start: int) -> tuple[int | None, bool]:
    """(year, is_forecast) stated next to the value at text[start:end]."""
    after = text[end:end + 60]
    m = _YEAR_AFTER_RE.match(after)
    if m:
        return int(m.group(2)), m.group(1).lower() in _FORECAST_PREPS
    m = _YEAR_PAREN_RE.match(after)
    if m:
        return int(m.group(1)), False
    before = text[max(s_start, start - 90):start]
    for rx in _YEAR_BEFORE_RES:
        m = rx.search(before)
        if m:
            return int(m.group(1)), False
    return None, False


def title_subject(title: str) -> str:
    """Market named in a page title ('Smartwatch Market Size, Share ...' -> 'Smartwatch')."""
    m = _SUBJECT_RE.search(title or "")
    return subject_phrase(title[: m.end()]) if m else ""


_subject_from_title = title_subject  # extract_values' `title_subject` flag shadows the name


def _slug_subject(source: str) -> str:
    """Market named in a URL slug ('.../smartwatch-market-106625' -> 'smartwatch')."""
    if not source.startswith("http"):
        return ""
    segments = [s for s in urlparse(source).path.split("/") if s]
    seg = next((s for s in reversed(segments) if "market" in s.lower()), segments[-1] if segments else "")
    seg = re.sub(r"\.(html?|aspx?|php)$", "", seg, flags=re.IGNORECASE)
    words = [w for w in re.split(r"[-_+.]+", seg) if w and not w.isdigit()]
    return title_subject(" ".join(words))


def extract_values(text: str, source: str, title: str = "", *, title_subject: bool = True) -> list[CitedValue]:
    """Every market-size figure in `text`, in US$ Mn, with the year next to it.

    With `title_subject`, a value whose sentence names no market takes its
    subject from the page title, else the URL slug (an exact-match report page
    often states 'Market Size, 2025: USD 16.6 Billion' with no market name) --
    unless the sentence is about a part of the market (a segment, region or
    share), which stays rejected.
    """
    text = text or ""
    found: list[CitedValue] = []
    spans = [(m.start(), m.end(), m.group(1), m.group(2)) for m in _VALUE_RE.finditer(text)]
    spans += [(m.start(), m.end(), m.group(1), m.group(2)) for m in _VALUE_AFTER_RE.finditer(text)
              if not any(s <= m.start() < e for s, e, *_ in spans)]
    page_subject: tuple[str, str] | None = None
    for start, end, number, unit in sorted(spans):
        try:
            value = float(number.replace(",", "")) * UNIT_TO_MN[unit.lower()]
        except (KeyError, ValueError):
            continue
        if value <= 0:
            continue
        s_start, s_end = _sentence_bounds(text, start)
        year, forecast = _year_for(text, start, end, s_start)
        sentence = text[s_start:s_end].strip()[:400]
        cv = CitedValue(
            source=source, raw_text=text[start:end].strip(), value_mn=value, year=year,
            sentence=sentence, subject=subject_phrase(text[s_start:start]),
        )
        if not cv.subject and title_subject:
            if page_subject is None:
                page_subject = (_subject_from_title(title), "page title")
                if not page_subject[0]:
                    page_subject = (_slug_subject(source), "url slug")
            if page_subject[0]:
                cv.subject, cv.subject_basis = page_subject
                if _PART_RE.search(sentence):
                    cv.reason = "value for a part of the market (segment/region/share), not the total"
        if cv.reason:
            pass
        elif forecast and year not in ACCEPTED_YEARS:
            cv.reason = f"forecast for {year}"
        elif _INCREMENT_RE.search(text[max(s_start, start - 40):start]):
            cv.reason = "incremental growth, not a market size"
        found.append(cv)
    return found


def extract_cagr(text: str) -> float | None:
    """Explicit 'CAGR of X%' in a text, as a decimal."""
    m = CAGR_RE.search(text or "")
    if not m:
        return None
    rate = float(m.group(1) or m.group(2)) / 100
    return rate if 0 < rate < 0.6 else None


def _implied_cagr(values: list[CitedValue]) -> float | None:
    """Growth implied by two dated values from one source ('USD 32.7 Bn in 2026
    ... USD 45.1 Bn by 2033')."""
    points = sorted({(v.year, v.value_mn) for v in values if v.year})
    if len(points) >= 2 and points[-1][0] > points[0][0] and points[0][1] > 0:
        (y0, v0), (y1, v1) = points[0], points[-1]
        rate = (v1 / v0) ** (1 / (y1 - y0)) - 1
        return rate if 0 < rate < 0.6 else None
    return None


# --- source tiers --------------------------------------------------------------- #
# Tier 2 = where a figure is re-posted rather than published: social sites,
# press-release mirrors, news and blogs. A rule about the kind of site, not a
# list of preferred publishers.
_TIER2_DOMAIN_RE = re.compile(
    r"linkedin|pinterest|medium\.com|einnews|einpresswire|openpr|globenewswire|prnewswire|businesswire|"
    r"newswire|prweb|yahoo|msn\.com|blogspot|wordpress|substack|slideshare|scribd|reddit|quora|facebook|"
    r"twitter|x\.com|youtube|accio|news|press|daily|times|post|tribune|herald|blog|wiki",
    re.IGNORECASE,
)
_TIER1_PATH_RE = re.compile(
    r"/(reports?|industry-analysis|industry-reports?|market-research|market-reports?|research-reports?|"
    r"market-analysis)(?:/|-|\.|$)",
    re.IGNORECASE,
)
_RESEARCH_DOMAIN_RE = re.compile(r"research|insight|intelligence|markets?|analytic|consult|reports?|forecast",
                                 re.IGNORECASE)


def source_tier(source: str) -> int:
    """1 = a market-research report page or the CMI report DB; 2 = anything else."""
    if source.startswith("CMI report DB"):
        return 1
    if not source.startswith("http"):
        return 2
    parsed = urlparse(source)
    domain = parsed.netloc.lower().removeprefix("www.")
    if _TIER2_DOMAIN_RE.search(domain):
        return 2
    if _TIER1_PATH_RE.search(parsed.path):
        return 1
    return 1 if _RESEARCH_DOMAIN_RE.search(domain.rsplit(".", 1)[0]) else 2


# --- scope -> search name -------------------------------------------------------- #
def scope_search_name(market_name: str, definition: str, llm) -> str:
    """The market name publishers would title a report on exactly this scope
    ('Urea' for 'total urea, fertilizer + industrial uses', where the market
    name 'Urea Fertilizer' would find fertilizer-only reports). Used for the
    web queries and the DB lookup. Falls back to the market name."""
    if not definition.strip() or llm is None:
        return market_name
    system = (
        "Given a market name and a written definition of its scope, return the short name that "
        "market-research publishers use in report titles for EXACTLY that scope (no 'Global', no "
        "'Market'). If the definition is broader or narrower than the name, the title name must "
        "follow the definition."
    )
    user = (
        f"Market name: {market_name}\nDefinition: {definition}\n\n"
        'Return JSON: {"search_name": "<1-5 words>"}'
    )
    try:
        name = str(llm.complete_json(system, user).get("search_name") or "").strip()
    except Exception:  # noqa: BLE001
        return market_name
    name = re.sub(r"(?i)^global\s+|\s+market$", "", name).strip()
    if not name or len(name.split()) > 6 or not re.search(r"[A-Za-z]", name):
        return market_name
    return name


# --- size check ------------------------------------------------------------------ #
MAGNITUDE_FACTOR = 3.0


def estimate_size_range(market_name: str, definition: str, llm) -> dict[str, Any] | None:
    """Independent size range from global annual units x average selling price
    (one LLM call). None when the call fails or the answer is not usable --
    the check is skipped then; it never blocks a run."""
    system = (
        "You are a market-sizing analyst. Without quoting any published market-size figure, estimate "
        "the global annual volume for this market (units sold per year; for a service, paying "
        "customers or subscriptions) and the average selling price per unit in US$ (for a service, "
        "annual revenue per customer). Give a realistic low and high for each."
    )
    user = (
        f"Market: {market_name}\nScope: {definition or market_name}\n\n"
        'Return JSON: {"unit": "<what one unit is>", "units_low": number, "units_high": number, '
        '"asp_low_usd": number, "asp_high_usd": number}'
    )
    try:
        data = llm.complete_json(system, user)
        u_lo, u_hi = float(data["units_low"]), float(data["units_high"])
        p_lo, p_hi = float(data["asp_low_usd"]), float(data["asp_high_usd"])
    except Exception:  # noqa: BLE001 - a failed check must never stop generation
        return None
    if not all(math.isfinite(x) and x > 0 for x in (u_lo, u_hi, p_lo, p_hi)):
        return None
    u_lo, u_hi = sorted((u_lo, u_hi))
    p_lo, p_hi = sorted((p_lo, p_hi))
    lo, hi = u_lo * p_lo / 1e6, u_hi * p_hi / 1e6
    # Nonsense guards: a range spanning >1000x says nothing; a global market
    # below US$1M or above US$10 trillion is not an estimate.
    if hi / lo > 1000 or lo < 1 or hi > 10_000_000:
        return None
    return {
        "unit": str(data.get("unit", "")), "units_low": u_lo, "units_high": u_hi,
        "asp_low_usd": p_lo, "asp_high_usd": p_hi, "low_mn": round(lo, 1), "high_mn": round(hi, 1),
    }


def outside_size_range(value_mn: float, size_range: dict[str, Any] | None,
                       factor: float = MAGNITUDE_FACTOR) -> bool:
    if not size_range:
        return False
    return value_mn < size_range["low_mn"] / factor or value_mn > size_range["high_mn"] * factor


def range_mid(size_range: dict[str, Any]) -> float:
    """Geometric middle of the size range (the range is multiplicative)."""
    return math.sqrt(size_range["low_mn"] * size_range["high_mn"])


# --- consensus ---------------------------------------------------------------- #
def consolidate(values: list[float], factor: float = OUTLIER_FACTOR) -> tuple[float | None, list[int], list[int]]:
    """(anchor, kept indices, dropped indices). Values more than `factor`x from
    the median are dropped; the anchor is the median of the rest (or the one
    value left)."""
    if not values:
        return None, [], []
    med = median(values)
    kept = [i for i, v in enumerate(values) if med / factor <= v <= med * factor]
    dropped = [i for i in range(len(values)) if i not in kept]
    if not kept:
        return None, [], dropped
    anchor = median([values[i] for i in kept]) if len(kept) >= 2 else values[kept[0]]
    return anchor, kept, dropped


DISPUTED_RATIO = 2.0


@dataclass
class CitedAnchor:
    value_2025_mn: float | None
    cagr: float | None
    method: str                        # "median of N cited values" | "single cited value" | "disputed: ..." | "none"
    accepted: list[CitedValue] = field(default_factory=list)
    rejected: list[CitedValue] = field(default_factory=list)
    corroborating: list[CitedValue] = field(default_factory=list)
    disputed: bool = False
    size_range: dict[str, Any] | None = None

    @property
    def grounded(self) -> bool:
        return self.value_2025_mn is not None

    def sources(self) -> list[str]:
        return [v.source for v in self.accepted]

    def describe(self) -> str:
        if not self.grounded:
            return "no usable cited figure"
        parts = "; ".join(
            f"{v.source}: {v.raw_text} ({v.year}) -> ${v.value_2025_mn:,.0f}M in 2025" for v in self.accepted
        )
        return f"{self.method} = ${self.value_2025_mn:,.0f}M [{parts}]"

    def to_dict(self) -> dict[str, Any]:
        return {
            "value_2025_mn": self.value_2025_mn, "cagr": self.cagr, "method": self.method,
            "disputed": self.disputed, "size_range": self.size_range,
            "accepted": [v.to_dict() for v in self.accepted],
            "corroborating": [v.to_dict() for v in self.corroborating],
            "rejected": [v.to_dict() for v in self.rejected],
        }


def llm_scope_verdicts(candidates: list[CitedValue], market_name: str, definition: str, llm) -> dict[int, ScopeVerdict]:
    """One LLM call judging each candidate's sentence against a written market
    definition. Returns {index: verdict}; empty on any failure (the caller then
    falls back to the keyword rules)."""
    if not candidates:
        return {}
    items = [{"id": i, "subject": c.subject, "sentence": c.sentence[:300]} for i, c in enumerate(candidates)]
    system = (
        "You check whether a cited market-size figure covers the same scope as the market being "
        "sized. Compare the market named in each sentence with the market definition. Answer "
        "'same' only when the scopes match; 'narrower' when the sentence covers a sub-segment "
        "(e.g. surgical gloves vs all medical gloves, urea fertilizer vs all urea uses); 'broader' "
        "when it covers more; 'different' when it is another market or a single region."
    )
    user = (
        f"Market: {market_name}\nDefinition: {definition}\n\nCandidates: {json.dumps(items, ensure_ascii=False)}\n\n"
        'Return JSON: {"verdicts": [{"id": number, "scope": "same|narrower|broader|different", "reason": "<short>"}]}'
    )
    try:
        data = llm.complete_json(system, user)
    except Exception:  # noqa: BLE001 - a failed check must not stop generation
        return {}
    out: dict[int, ScopeVerdict] = {}
    for v in data.get("verdicts", []) or []:
        try:
            idx = int(v.get("id"))
        except (TypeError, ValueError):
            continue
        scope = str(v.get("scope", "")).lower()
        out[idx] = ScopeVerdict(scope == "same", f"{scope}: {v.get('reason', '')}".strip())
    return out


def _reject(v: CitedValue, reason: str, rejected: list[CitedValue]) -> None:
    v.status, v.reason = "rejected", reason
    rejected.append(v)


def _corroborate(v: CitedValue) -> None:
    v.status, v.reason = "corroborating", "tier-2 source: corroborates only, never the anchor"


def collect_cited_values(
    sources: list[tuple[str, ...]],
    market_name: str,
    *,
    definition: str = "",
    llm=None,
    size_range: dict[str, Any] | None = None,
    title_subject: bool = True,
    source_tiers: bool = True,
    weak_consensus: bool = False,
) -> CitedAnchor:
    """Consensus 2025 market size from `[(source_label, text[, page_title]), ...]`.

    One figure per source (2025 preferred, then 2024, then 2026), moved to 2025
    at the source's own CAGR (explicit, else implied by its dated values, else
    the median CAGR across sources, else 5%). Repeats of the same figure across
    sources (syndicated press releases) count once. Then, each step optional:
      size_range      reject figures >3x outside the units x ASP range
      source_tiers    anchor on research-report pages / the CMI DB; other
                      sites only corroborate unless there are fewer than 2
                      tier-1 values, and never anchor alone
      weak_consensus  one value or a >2x spread is "disputed"; the anchor is
                      then the value closest to the size-range middle, else
                      the CMI DB figure, else the median
    """
    per_source: list[tuple[str, list[CitedValue], float | None]] = []
    for item in sources:
        label, text = item[0], item[1]
        title = item[2] if len(item) > 2 else ""
        vals = extract_values(text, label, title, title_subject=title_subject)
        if vals:
            per_source.append((label, vals, extract_cagr(text) or _implied_cagr(vals)))
    all_cagrs = [c for _, _, c in per_source if c]
    fallback_cagr = median(all_cagrs) if all_cagrs else None

    rejected: list[CitedValue] = []
    candidates: list[CitedValue] = []
    for _label, vals, cagr in per_source:
        for v in vals:
            if v.reason:
                _reject(v, v.reason, rejected)
            elif v.year is None:
                _reject(v, "no year stated next to the value", rejected)
            elif v.year not in ACCEPTED_YEARS:
                _reject(v, f"year {v.year} outside {ACCEPTED_YEARS[0]}-{ACCEPTED_YEARS[-1]}", rejected)
            else:
                if cagr:
                    v.cagr_used, v.cagr_basis = cagr, "cited"
                elif fallback_cagr:
                    v.cagr_used, v.cagr_basis = fallback_cagr, "evidence median"
                else:
                    v.cagr_used, v.cagr_basis = DEFAULT_CAGR, "default"
                v.value_2025_mn = round(v.value_mn * (1 + v.cagr_used) ** (TARGET_YEAR - v.year), 1)
                candidates.append(v)

    use_llm = bool(definition.strip()) and llm is not None
    llm_verdicts: dict[int, ScopeVerdict] = {}
    if use_llm:
        prelim = [c for c in candidates if check_scope(c.subject, market_name, keyword_rules=False).ok]
        verdicts = llm_scope_verdicts(prelim, market_name, definition, llm)
        llm_verdicts = {id(prelim[i]): v for i, v in verdicts.items() if 0 <= i < len(prelim)}
        use_llm = bool(llm_verdicts)

    scoped: list[CitedValue] = []
    for c in candidates:
        verdict = check_scope(c.subject, market_name, keyword_rules=not use_llm)
        if verdict.ok and use_llm:
            verdict = llm_verdicts.get(id(c), check_scope(c.subject, market_name))
        if not verdict.ok:
            _reject(c, f"scope: {verdict.reason}", rejected)
        else:
            c.reason = verdict.reason
            scoped.append(c)

    # One figure per source, then one per distinct figure, then the size check.
    order = {2025: 0, 2024: 1, 2026: 2}
    best_by_source: dict[str, CitedValue] = {}
    for c in scoped:
        cur = best_by_source.get(c.source)
        if cur is None or order[c.year] < order[cur.year]:
            if cur is not None:
                _reject(cur, "another figure from the same source was used", rejected)
            best_by_source[c.source] = c
        else:
            _reject(c, "another figure from the same source was used", rejected)
    unique: list[CitedValue] = []
    for c in best_by_source.values():
        dup = next((u for u in unique if u.year == c.year and abs(u.value_mn - c.value_mn) <= 0.005 * u.value_mn), None)
        if dup is not None:
            _reject(c, f"same figure as {dup.source} (counted once)", rejected)
        elif outside_size_range(c.value_2025_mn, size_range):
            _reject(c, f"magnitude: more than {MAGNITUDE_FACTOR:g}x outside the units x ASP range "
                       f"${size_range['low_mn']:,.0f}M-${size_range['high_mn']:,.0f}M", rejected)
        else:
            c.tier = source_tier(c.source)
            unique.append(c)

    corroborating: list[CitedValue] = []
    pool = unique
    if source_tiers:
        tier1 = [c for c in unique if c.tier == 1]
        if len(tier1) >= 2:
            pool, corroborating = tier1, [c for c in unique if c.tier != 1]
        elif not tier1:
            pool, corroborating = [], list(unique)
        for c in corroborating:
            _corroborate(c)

    anchor, kept, dropped = consolidate([c.value_2025_mn for c in pool])
    for i in dropped:
        _reject(pool[i], f"outlier: more than {OUTLIER_FACTOR}x from the median of cited values", rejected)
    accepted = [pool[i] for i in kept]
    if source_tiers and accepted and not any(c.tier == 1 for c in accepted):
        # Only tier-2 values survived the outlier step: they may not anchor alone.
        for c in accepted:
            _corroborate(c)
        corroborating += accepted
        accepted, anchor = [], None
    for c in accepted:
        c.status = "accepted"
    if anchor is None:
        return CitedAnchor(None, None, "none", [], rejected, corroborating, False, size_range)

    values = [c.value_2025_mn for c in accepted]
    disputed = len(values) == 1 or max(values) / min(values) > DISPUTED_RATIO
    method = f"median of {len(accepted)} cited values" if len(accepted) >= 2 else "single cited value"
    if disputed and weak_consensus and len(accepted) >= 2:
        db = [c for c in unique if c.source.startswith("CMI report DB")]
        if size_range:
            mid = range_mid(size_range)
            pick = min(accepted, key=lambda c: abs(math.log(c.value_2025_mn / mid)))
            anchor = pick.value_2025_mn
            method = f"disputed: value closest to units x ASP mid ${mid:,.0f}M ({pick.source})"
        elif db:
            anchor, method = db[0].value_2025_mn, "disputed: CMI report DB figure"
    cagrs = [c.cagr_used for c in accepted if c.cagr_basis == "cited" and c.cagr_used]
    return CitedAnchor(round(anchor, 1), median(cagrs) if cagrs else None, method, accepted, rejected,
                       corroborating, disputed, size_range)
