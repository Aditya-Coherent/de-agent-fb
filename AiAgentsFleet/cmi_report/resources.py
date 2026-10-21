"""'9.1 Sources', the first part of Research Methodology (changes.txt, 2026-10-19): the sources used to
research this market, in the VP's format a-h. Asked once per market and cached in
output/<slug>/resources.json.

Rules (the VP's prompt): authoritative, verifiable, market-specific sources only; no consulting firms or
commercial market-research publishers (they are dropped by name even if the model lists them); no
invented sources -- every named source is looked up with a web search and kept only when the results
name it. Primary-research interviews are respondent profiles (no names); the proprietary elements are
fixed text.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

VERSION = 3
MAX_ITEMS = 8              # per category
CATEGORIES = [  # (key, label as printed)
    ("primary_interviews", "Primary Research interviews"),
    ("databases", "Databases"),
    ("magazines", "Magazines"),
    ("journals", "Journals"),
    ("newspapers", "Newspapers"),
    ("associations", "Associations"),
    ("public_domain", "Public Domain sources"),
]
PROPRIETARY = ("Proprietary Elements", "CMI Data Analytics Tool, Proprietary CMI Existing Repository of "
                                       "information for last 10 years")

# consulting firms and commercial market-research publishers: never a source (VP rule 1, rule 4)
_BLOCKED = re.compile(
    r"grand ?view|fortune business|markets ?and ?markets|mordor|allied market|imarc|technavio|research ?and ?markets|"
    r"precedence|verified market|future market insights|fact\.?mr|persistence market|transparency market|"
    r"market research future|data ?bridge|straits research|emergen|dataintelo|polaris market|spherical insights|"
    r"zion market|global market insights|expert market research|cognitive market|the business research|"
    r"maximize market|skyquest|market\.us|astute analytica|introspective|bcc research|frost ?& ?sullivan|"
    r"mckinsey|boston consulting|\bbcg\b|\bbain\b|deloitte|pwc|pricewaterhouse|\bey\b|ernst|kpmg|accenture|gartner|"
    r"\bidc\b|euromonitor|kline|lux research|wood mackenzie|coherent market|statista|business research insights|"
    r"report ?linker|reportlinker|6wresearch|ken research|knowledge sourcing|market data forecast|"
    r"stratistics|credence research|vantage market|insightace|reports ?and ?data|marketresearch\.com",
    re.I)

_SYSTEM = ("You are a senior research librarian at Coherent Market Insights. You list only sources that "
           "really exist; you never invent publications, databases, organisations or URLs.")


def _prompt(market: str, m: dict) -> str:
    dims = m.get("dimensions") or {}
    segs = (m.get("segments") or {}).get("Global") or {}
    dfn = (m.get("definition") or {}).get("definition") or ""
    focus = ", ".join(c for r in ("Middle East", "Africa") for c in (m.get("countries") or {}).get(r, {})
                      if not c.startswith("Rest of"))
    return (
        f"I am preparing a professional {market} Report and need a comprehensive Sources section for the report.\n"
        f"Market definition: {dfn}\n"
        f"Segments: by {dims.get('type', {}).get('singular', 'type')}: {', '.join(segs.get('type') or [])}; "
        f"by {dims.get('demand', {}).get('singular', 'end use')}: {', '.join(segs.get('demand') or [])}.\n"
        f"Geography: global, with a deep dive on the Middle East & Africa ({focus}).\n\n"
        f"Identify and organize the most relevant and authoritative sources used to research the {market}: "
        f"credible, verifiable and domain-specific sources covering market dynamics, industry trends, "
        f"regulations, government policies, technology developments, production, supply chains, trade, pricing, "
        f"consumer behaviour, company developments and other factors relevant to this market.\n\n"
        "Rules:\n"
        "1. Do not mention or recommend consulting firms or commercial market-research companies (e.g. Grand "
        "View Research, Fortune Business Insights, MarketsandMarkets, Mordor Intelligence, Allied Market Research, "
        "IMARC, Technavio, Research and Markets, Precedence Research). Do not use third-party market-research "
        "reports; use the original/primary source.\n"
        "2. Prioritise government agencies, national statistical offices, international organisations, "
        "regulatory authorities, industry associations, academic institutions, peer-reviewed journals, official "
        "company filings and announcements, recognised trade publications, reputable newspapers, official "
        "databases and public government/institutional datasets.\n"
        "3. Do not hallucinate: include only sources that actually exist, are active and cover this market or "
        "domain. No invented publications, databases, organisations, interviews, reports, statistics or URLs.\n"
        "4. Do not use competitors (research or consulting firms) as sources for company-specific analysis.\n"
        "5. Customise to this market: its products, technologies, applications, regulations, supply chain, "
        "customers, distribution channels and geography (including the Middle East & Africa). No generic list; "
        "no irrelevant stakeholders just to fill a category: every item must publish on THIS product category or "
        "its direct inputs, users or regulation (no bodies about adjacent products, and no general-interest "
        "bodies, unless they cover this product). Newspapers: business and industry-trade press for this "
        "market's sector, including titles from the Middle East and Africa that report on it -- no "
        "general-interest titles. Public domain: include the regulators / ministries responsible for this market "
        "in BOTH Middle East and African countries covered above, plus the main global and EU/US frameworks.\n\n"
        "Categories (4-8 items each where genuinely relevant, NEVER more than 8; most relevant first, mixing "
        "global and Middle East & Africa sources):\n"
        "a. primary_interviews: respondent PROFILES interviewed (job roles and organisation types, no personal or "
        f"company names), e.g. 'Regional sales managers at {market.lower().removesuffix(' market').strip()} "
        f"manufacturers'\n"
        "b. databases  c. magazines (trade publications)  d. journals (peer-reviewed)  e. newspapers  "
        "f. associations  g. public_domain (government, regulatory and international-organisation sources)\n\n"
        'Return {"primary_interviews": ["..."], "databases": [{"name": "...", "domain": "<official website '
        'domain if known, else empty>"}], "magazines": [...], "journals": [...], "newspapers": [...], '
        '"associations": [...], "public_domain": [...]}  -- every non-interview item as {name, domain}.')


def _tokens(name: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", name.lower()) if len(w) > 2 and w not in {"the", "and", "for"}}


def _acronym(name: str) -> str:
    """'(FAO)' / 'FAOSTAT' style short names a result may use instead of the full name."""
    mm = re.search(r"\(([A-Z][A-Za-z&]{1,9})\)", name)
    return mm.group(1).lower() if mm else ""


def verified(name: str, domain: str, search, strict: bool = True) -> bool | None:
    """True when a web search for the source returns a result that names it (its exact name, every word of
    it in a title, its acronym, or its official domain); None when the search returned nothing (backend
    down). `strict=False` (descriptive names such as regulatory frameworks): three quarters of the name's
    words in one result is enough."""
    hits = search(f'"{re.sub(r"\s*\([^)]*\)", "", name).strip()}"')
    if not hits:
        return None
    bare = re.sub(r"\s*\([^)]*\)", "", name).strip().lower()
    want, acro = _tokens(bare), _acronym(name)
    dom = (domain or "").lower().removeprefix("www.")
    for h in hits:
        text = " ".join(re.findall(r"[a-z0-9&]+", f"{h.get('title', '')} {h.get('text', '')}".lower()))
        # the name itself, in order ('AgriBusiness Global Middle East & Africa' is not matched by a page
        # about AgriBusiness Global that also mentions the Middle East)
        if bare and " ".join(re.findall(r"[a-z0-9&]+", bare)) in text:
            return True
        # or every word of the name in the result's title ('UN Comtrade Database' -> 'Database - UN Comtrade')
        if want and want <= set(re.findall(r"[a-z0-9]+", (h.get("title") or "").lower())):
            return True
        if not strict and want and len(want & set(text.split())) >= max(2, round(0.75 * len(want))):
            return True
        if acro and acro in text.split():
            return True
        if dom and dom in (h.get("domain") or "").lower():
            return True
    return False


def _default_search(query: str) -> list[dict]:
    from me_engine.generation.web_evidence import search_snippets_with_sources
    return search_snippets_with_sources(query, max_results=4)


def build(raw: dict, search=None, log: list | None = None) -> list[tuple[str, list[str]]]:
    """[(label, [items])] in the a-h order: blocked names dropped, every named source verified by search;
    the proprietary elements last (fixed text)."""
    search = search or _default_search
    log = log if log is not None else []
    out = []
    for key, label in CATEGORIES:
        items = []
        for it in (raw or {}).get(key) or []:
            name = str((it.get("name") or "") if isinstance(it, dict) else it).strip().rstrip(".")
            if not name or name.lower() in {i.lower() for i in items}:
                continue
            if _BLOCKED.search(name) or (isinstance(it, dict) and _BLOCKED.search(it.get("domain") or "")):
                log.append(f"resources: '{name}' left out (consulting / market-research publisher)")
                continue
            if key != "primary_interviews":
                ok = verified(name, it.get("domain", "") if isinstance(it, dict) else "", search)
                if ok is False:
                    log.append(f"resources: '{name}' left out (not found by web search)")
                    continue
                if ok is None:
                    log.append(f"resources: '{name}' kept unverified (web search returned nothing)")
            items.append(name)
            if len(items) == MAX_ITEMS:
                break
        if items:
            out.append((label, items))
    return out + [(PROPRIETARY[0], [PROPRIETARY[1]])]


_REVIEW_SYSTEM = ("You are a strict research editor. You check a report's source list for relevance to the "
                  "market and for sources that do not exist as named.")


def review(market: str, sections: list, llm, log: list) -> list:
    """Second pass (VP rules 3 and 5): items that do not cover this market or its direct inputs, users or
    regulation, or that do not exist under that exact name, are removed. Interviews and the proprietary
    elements are not reviewed."""
    named = {label: items for label, items in sections if label not in ("Primary Research interviews",
                                                                        PROPRIETARY[0])}
    try:
        ans = llm.complete_json(_REVIEW_SYSTEM, (
            f"Market: {market}\nSource list: {json.dumps(named, ensure_ascii=False)}\n\n"
            "List the items to REMOVE: (a) not relevant to this market (e.g. a body about seeds, pesticides only, "
            "or a general-interest publication that does not cover it), or (b) not a real source under exactly "
            "this name (e.g. an invented regional edition). Keep everything else.\n"
            'Return {"remove": [{"item": <exact text>, "why": "..."}]}'))
    except Exception as exc:                          # no review: the verified list stands
        log.append(f"resources: relevance review skipped ({exc})")
        return sections
    drop = {str(x.get("item", "")).strip().lower(): x.get("why", "") for x in ans.get("remove") or []
            if isinstance(x, dict)}
    out = []
    for label, items in sections:
        keep = [it for it in items if it.lower() not in drop or label == PROPRIETARY[0]]
        log += [f"resources: '{it}' left out (review: {drop[it.lower()]})" for it in items if it not in keep]
        if keep:
            out.append((label, keep))
    return out


def ensure(market_dir: Path, market: str, m: dict, llm, refresh: bool = False, search=None) -> dict:
    """{'raw': the model's answer, 'sections': build(...), 'log': [...]}, cached in resources.json."""
    path = market_dir / "resources.json"
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if old.get("version") == VERSION and not refresh:
        return old
    try:
        raw = llm.complete_json(_SYSTEM, _prompt(market, m))
    except Exception as exc:                          # no answer: the chapter is left out, not the report
        raw = {"error": str(exc)}
    log: list[str] = []
    out = {"version": VERSION, "raw": raw, "sections": review(market, build(raw, search, log), llm, log), "log": log}
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    return out
