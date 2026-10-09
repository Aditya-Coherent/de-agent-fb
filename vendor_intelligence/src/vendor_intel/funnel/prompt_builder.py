"""Build short, de-duplicated web search prompts — generic rules only (no industry branches)."""
from __future__ import annotations

import re

_FILLER_ADJ = re.compile(
    r"\b(?:leading|top|best|popular|biggest|main|major|artisanal|industrial|"
    r"commercial|rural|publicly\s+listed|fast[\s-]?growing|niche|specialty)\b",
    re.I,
)
_ROLE_NOUN = re.compile(
    r"\b(?:manufacturers?|suppliers?|exporters?|producers?|vendors?|"
    r"companies|brands?|startups?|distributors?|OEMs?)\b",
    re.I,
)
_MAX_PROMPT_LEN = 72   # ~7 words × avg 10 chars — shorter queries fail less on DDGS
_MAX_PROMPT_WORDS = 7  # hard word-count cap to avoid DDGS timeouts on long queries

# Common English modifiers that pollute search (many meanings) — not industry names
_AMBIGUOUS_TOKENS = frozenset(
    {
        "modular",
        "industrial",
        "rural",
        "commercial",
        "digital",
        "advanced",
        "modern",
        "traditional",
        "generic",
        "specialty",
        "niche",
        "premium",
        "professional",
        "global",
        "local",
        "new",
        "smart",
    }
)

# Optional single-word swaps to diversify queries (language-level, not domain-specific)
_TOKEN_ALTERNATES: dict[str, str] = {
    "equipment": "machinery",
    "machinery": "equipment",
    "suppliers": "vendors",
    "vendors": "suppliers",
    "manufacturers": "producers",
    "producers": "manufacturers",
    "harvesting": "harvest",
    "harvest": "harvesting",
    "cooling": "chiller",
}


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _clamp(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    # Enforce word-count cap first (shorter = fewer DDGS failures)
    words = text.split()
    if len(words) > _MAX_PROMPT_WORDS:
        text = " ".join(words[:_MAX_PROMPT_WORDS])
    if len(text) > _MAX_PROMPT_LEN:
        text = text[:_MAX_PROMPT_LEN].rsplit(" ", 1)[0]
    return text


def _q(*parts: str) -> str:
    return _clamp(" ".join(p for p in parts if p))


def distill_search_topic(market: str) -> str:
    """Product phrase without filler adjectives or role nouns."""
    t = _FILLER_ADJ.sub("", market.strip())
    t = _ROLE_NOUN.sub("", t)
    t = re.sub(r"\s+", " ", t).strip(" ,-")
    return t if len(t) >= 3 else market.strip()


def _strip_ambiguous_modifiers(phrase: str) -> str:
    words = phrase.split()
    if len(words) <= 2:
        return phrase
    filtered = [w for w in words if w.lower() not in _AMBIGUOUS_TOKENS]
    return " ".join(filtered) if filtered else phrase


def _best_ngram_phrase(phrase: str, min_n: int = 2, max_n: int = 3) -> str:
    """Pick the most informative 2–3 word span (most non-ambiguous tokens)."""
    words = phrase.split()
    if len(words) <= max_n:
        return phrase
    best = phrase
    best_score = -1
    for n in range(max_n, min_n - 1, -1):
        for i in range(len(words) - n + 1):
            chunk = " ".join(words[i : i + n])
            toks = _tokenize(chunk)
            score = sum(1 for t in toks if t not in _AMBIGUOUS_TOKENS and len(t) >= 3)
            if score > best_score:
                best_score = score
                best = chunk
    return best


def refine_search_topic(market: str, geo: str = "") -> str:
    """
    Shorter search-optimized topic: drop role words, ambiguous modifiers,
    and prefer the strongest multi-word phrase from the market text.
    """
    del geo
    base = distill_search_topic(market)
    trimmed = _strip_ambiguous_modifiers(base)
    if len(_tokenize(trimmed)) >= 3:
        core = _best_ngram_phrase(trimmed)
        if len(_tokenize(core)) >= 2:
            return core
    return trimmed if len(trimmed) >= 3 else base


def _alternate_phrase(phrase: str) -> str:
    """One variant by swapping a single token to a near-synonym, if available."""
    words = phrase.split()
    for i, w in enumerate(words):
        alt = _TOKEN_ALTERNATES.get(w.lower())
        if alt:
            words[i] = alt
            return " ".join(words)
    return phrase


def _short_phrase(phrase: str, max_words: int = 4) -> str:
    words = [w for w in phrase.split() if w.lower() not in _AMBIGUOUS_TOKENS]
    return " ".join(words[:max_words]) if words else phrase


def _acronym_from_phrase(phrase: str) -> str:
    """Initialism from phrase words (e.g. multi-word technical topics)."""
    words = [w for w in re.findall(r"[A-Za-z0-9]+", phrase) if len(w) >= 2]
    if len(words) < 3:
        return ""
    letters = "".join(w[0].upper() for w in words[:6] if w[0].isalpha())
    return letters if 3 <= len(letters) <= 8 else ""


def topic_variants(market: str, geo: str = "") -> list[str]:
    """Several phrasings for diverse search prompts — all derived from market text."""
    primary = refine_search_topic(market, geo)
    variants: list[str] = []
    seen: set[str] = set()

    def add(p: str) -> None:
        key = " ".join(p.lower().split())
        if p and key not in seen and len(p) >= 3:
            seen.add(key)
            variants.append(p)

    add(primary)
    add(_strip_ambiguous_modifiers(distill_search_topic(market)))
    add(_alternate_phrase(primary))
    short = _short_phrase(primary)
    if short != primary:
        add(short)
    ac = _acronym_from_phrase(distill_search_topic(market))
    if ac:
        add(ac)
    return variants[:5]


def geo_search_label(geo: str) -> str:
    if not geo or geo.lower() == "global":
        return ""
    parts = [p.strip() for p in geo.split(",") if p.strip()]
    if len(parts) >= 2:
        return parts[0]
    return geo.strip()


def _market_has_role(market: str, stem: str) -> bool:
    return stem in market.lower()


def build_funnel_prompts(
    market: str,
    geo: str,
    *,
    industry_terms: list[str] | None = None,
) -> list[dict[str, str]]:
    terms = [t for t in (industry_terms or []) if t and len(str(t).strip()) >= 3]
    topic = terms[0] if terms else refine_search_topic(market, geo)
    g = geo_search_label(geo)

    l0 = _q(topic, "companies", g)
    if _market_has_role(market, "supplier"):
        l1 = _q(topic, g)
    elif _market_has_role(market, "manufacturer"):
        l1 = _q(topic, "OEM", g)
    elif _market_has_role(market, "export"):
        l1 = _q(topic, "exporters", g)
    elif _market_has_role(market, "producer"):
        l1 = _q(topic, "producers", g)
    else:
        l1 = _q(topic, "manufacturers", g)
    l2 = _q("top", topic, "competitors", g)

    return [
        {"id": "L0", "level": "L0", "text": l0},
        {"id": "L1", "level": "L1", "text": l1},
        {"id": "L2", "level": "L2", "text": l2},
    ]


def build_competitor_search_prompts(
    market: str,
    geo: str,
    *,
    anchor_company: str | None = None,
) -> list[str]:
    """Generic competitor-discovery queries for the topic and optional focal company."""
    topic = refine_search_topic(market, geo)
    g = geo_search_label(geo)
    seen: set[str] = set()
    out: list[str] = []

    def add(*parts: str) -> None:
        text = _q(*parts)
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            out.append(text)

    add(topic, "competitors", g)
    add("top", topic, "competitors", g)
    add(topic, "competitive landscape", g)

    anchor = (anchor_company or "").strip()
    if anchor:
        add(anchor, "competitors", g)
        add("competitors of", anchor, g)
        add(anchor, "alternatives", g)

    return out


def build_discovery_prompts(
    market: str,
    geo: str,
    *,
    max_prompts: int = 9,
    anchor_company: str | None = None,
    industry_terms: list[str] | None = None,
) -> list[dict[str, str]]:
    # CHANGED: function-type-diverse fallback prompts (SOP-aligned)
    g = geo_search_label(geo)
    topic = refine_search_topic(market, geo)
    extra = [t for t in (industry_terms or []) if t and len(t) >= 3]
    kw = extra[0] if extra else topic

    seen: set[str] = set()
    out: list[dict[str, str]] = []

    def push(text: str) -> None:
        key = " ".join(text.lower().split())
        if not text or key in seen or len(text) < 5:
            return
        seen.add(key)
        out.append({"id": f"P{len(out) + 1}", "level": "discovery", "text": text})

    for comp_q in build_competitor_search_prompts(
        market, geo, anchor_company=anchor_company
    ):
        if len(out) >= max_prompts:
            break
        push(comp_q)

    # Value chain roles — explicit function words in each query
    _FUNCTION_ROLES = [
        "manufacturers",
        "distributors",
        "wholesalers",
        "importers",
        "exporters",
        "retailers dealers",
        "service providers",
        "system integrators",
        "consultants",
        "vendors suppliers",
        "resellers partners",
    ]
    for role in _FUNCTION_ROLES:
        if len(out) >= max_prompts:
            break
        push(_q(kw, role, g))

    if len(out) < max_prompts:
        push(_q("complete list", kw, "companies", g))
    if len(out) < max_prompts:
        push(_q("top", kw, "companies", g, "market share ranking"))
    if len(out) < max_prompts:
        push(_q(kw, "B2B directory", g))

    for term in extra[1:3]:
        if len(out) >= max_prompts:
            break
        push(_q(term, "companies", g))

    for i, row in enumerate(out):
        row["id"] = f"P{i + 1}"

    return out[:max_prompts]


def build_widen_prompts(
    market: str,
    geo: str,
    *,
    anchor_company: str | None = None,
) -> list[dict[str, str]]:
    """Extra searches when discovery returns too few companies."""
    topic = refine_search_topic(market, geo)
    g = geo_search_label(geo)
    alts = topic_variants(market, geo)
    second = alts[1] if len(alts) > 1 else _alternate_phrase(topic)
    rows = [
        {"id": "W1", "level": "discovery", "text": _q(topic, "competitor companies", g)},
        {"id": "W2", "level": "discovery", "text": _q(second, "supplier companies", g)},
    ]
    anchor = (anchor_company or "").strip()
    if anchor:
        rows.append(
            {
                "id": "W3",
                "level": "discovery",
                "text": _q(anchor, "competitors", g),
            }
        )
    return rows


def core_anchor_tokens(market: str, search_topic: str | None = None) -> dict[str, set[str]]:
    """
    Anchors for relevance: single tokens + required multi-word phrases.
    All derived from search_topic text.
    """
    topic = (search_topic or refine_search_topic(market)).lower()
    words = topic.split()
    singles = {
        t
        for t in _tokenize(topic)
        if t not in _AMBIGUOUS_TOKENS and len(t) >= 3
    }
    # Prefer longer tokens; keep shorter ones only if no long tokens exist
    long_tokens = {t for t in singles if len(t) >= 4}
    if long_tokens:
        singles = long_tokens | {t for t in singles if len(t) == 3 and t not in _AMBIGUOUS_TOKENS}

    phrases: set[str] = set()
    for n in (2, 3):
        for i in range(len(words) - n + 1):
            chunk = " ".join(words[i : i + n]).lower()
            toks = _tokenize(chunk)
            if len(chunk) >= 6 and toks and toks[0] not in _AMBIGUOUS_TOKENS:
                phrases.add(chunk)

    if not singles:
        singles = set(_tokenize(topic))
    return {"tokens": singles, "phrases": phrases, "ambiguous": set(_AMBIGUOUS_TOKENS) & set(_tokenize(topic))}
