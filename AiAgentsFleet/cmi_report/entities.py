"""Vendor facts the report may print (PLAN-05, FINDINGS-2026-10-15 X9, X10, B3,
C6-C9, D2, D7, E1, F3).

- Shares: a vendor share is printed only when a quote in the evidence states
  that company's share of THIS market (or its revenue in it). The quote must be
  in a snippet the agent was shown and the figure must be in the quote -- the
  same rule as run_data._verified_published (verify_size._find). check_shares
  / check_shares_with_reask (fleet competitive_landscape) and
  review_vendor_answer (CMI vendors group, MEA shares) apply it, re-asking once.
- Entities: one targeted search + one LLM call per vendor (VENDOR_CAP at most)
  for its HQ, parent group, business type and whether it earns the revenue the
  definition measures. HQ, parent and business type count only when a snippet
  backs them (`verified`); an unverified vendor is left out of every table.
- One exclusion list (exclusions) for every vendor list, global and MEA.
- Plausibility (plausibility): a mainly-domestic vendor's implied revenue may
  not exceed its home country's market (Alibaba > China); an unsourced share
  above 20% is blocked.
- review: m["vendor_review"] -- blockers (PLAN-08 turns them into release-gate
  failures) and warnings.

No number is estimated, blended or rescaled here.
"""
from __future__ import annotations

import json
import re
import time
from typing import Callable

VENDOR_CAP = 12                     # entity checks per market (one LLM call each)
UNSOURCED_MAX_PCT = 20.0            # an unsourced share above this is blocked
MEA_PRESENCE = ("Leading", "Strong", "Moderate", "Limited", "None")

# --------------------------------------------------------------------------- #
# names
# --------------------------------------------------------------------------- #
_SUFFIX = re.compile(r"\b(?:inc|incorporated|ltd|limited|llc|plc|corp|corporation|co|company|s\.?a|se|ag|nv|bv|"
                     r"gmbh|public|holdings?|group|jsc|pjsc|s\.?p\.?a|spa|asa|a/s|ab|oyj|kgaa|lp|pte|pvt|"
                     r"private|joint stock)\b\.?", re.I)
_OTHER = re.compile(r"^\s*(?:regional\s*&\s*other|others?|regional and other|rest)\b", re.I)


def is_other(name: str) -> bool:
    return bool(_OTHER.match(str(name or "")))


def _simple(s: str, case: bool = False) -> str:
    if case:
        return re.sub(r"[^A-Za-z0-9]+", " ", s or "").strip()
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def name_keys(name: str, loose: bool = True, case: bool = False) -> set[str]:
    """The ways a company is written ('Charoen Pokphand Foods Public Co. Ltd (CPF)'
    -> {'charoen pokphand foods public co ltd', 'charoen pokphand foods', 'cpf'}).
    `loose` adds acronyms inside the name ('CSPC Pharmaceutical Group' -> 'cspc')
    for finding the company in text; comparing two companies uses the strict
    keys, since a brand shared by a parent and its subsidiary ('RYNAN') does not
    make them one company. `case` keeps the capitals ('Egyptian Shrimp')."""
    n = str(name or "").strip()
    if not n:
        return set()
    keys = set()
    base = n.split(" (")[0].strip()
    for part in (base, base.split(",")[0]):
        keys.add(_simple(part, case))
        stripped = _simple(_SUFFIX.sub(" ", part), case)
        if stripped:
            keys.add(stripped)
    # an acronym in brackets is another name for the company ('(NAQUA)', '(CPF)'); a group name is not
    for inner in re.findall(r"\(([^)]+)\)", n):
        if re.fullmatch(r"[A-Z][A-Z0-9&.-]{1,9}", inner.strip()):
            keys.add(_simple(inner, case))
    # in text, so is an acronym inside the name ('CSPC Pharmaceutical Group' -> 'cspc', 'Zhejiang NHU' -> 'nhu')
    for tok in re.findall(r"\b[A-Z][A-Z0-9]{2,7}\b", base) if loose else []:
        if not _SUFFIX.fullmatch(tok):
            keys.add(tok if case else tok.lower())
    return {k for k in keys if len(k) >= 3}


def same_vendor(a: str, b: str) -> bool:
    return bool(name_keys(a, loose=False) & name_keys(b, loose=False))


def lookup(name: str, table: dict) -> object | None:
    """table[x] for the key x that names the same company as `name`."""
    for k, v in (table or {}).items():
        if same_vendor(k, name):
            return v
    return None


def named_in(text: str, name: str, case: bool = False) -> bool:
    """`text` names the company (any of its keys, as whole words). `case`: with
    its capitals, so 'Egyptian shrimp farms' does not name 'Egyptian Shrimp Company'."""
    low = " " + _simple(text, case) + " "
    return any(f" {k} " in low for k in name_keys(name, case=case))


# --------------------------------------------------------------------------- #
# shares: sourced or not printed
# --------------------------------------------------------------------------- #
def _years(text: str) -> set[int]:
    return {int(y) for y in re.findall(r"(?<!\d)(20[0-4]\d)(?!\d)", text or "")}


def verify_share(row: dict, snippets: list[dict], market: str | None, year: int) -> dict:
    """{"status": "sourced" | "unsourced" | "rejected", "why", ...} for one row
    {"name"|"vendor", "share_pct", "revenue_usd_mn"?, "share_quote"|"quote"}.
    Sourced: the quote is in a snippet, names the company and the market,
    states the share (as a %) or the revenue (in USD), and states no year
    other than `year`."""
    from AiAgentsFleet.cmi_report.verify_size import _find, _plain, amounts_usd_mn
    from AiAgentsFleet.sources import market_tokens
    name = str(row.get("name") or row.get("vendor") or "")
    quote = str(row.get("share_quote") or row.get("quote") or "").strip()
    share, rev = row.get("share_pct"), row.get("revenue_usd_mn")
    out = {"vendor": name, "status": "unsourced", "why": "no quote"}
    if not quote:
        return out
    src = _find(quote, snippets)
    q = _plain(quote)
    stems = market_tokens(market or "")
    if not src:
        why = "quote not found in any search snippet"
    elif not named_in(q, name):
        why = "quote does not name the company"
    elif stems and not any(st in q.lower() for st in stems):
        why = "quote is not about this market"
    elif _years(q) and year not in _years(q):
        why = f"quote states {', '.join(map(str, sorted(_years(q))))}, not {year}"
    elif isinstance(share, (int, float)) and re.search(
            rf"(?<![\d.]){re.escape(f'{float(share):g}')}\s*(?:%|per\s?cent)", q, re.I):
        return {"vendor": name, "status": "sourced", "share_pct": float(share), "quote": quote,
                "url": src.get("url"), "domain": src.get("domain")}
    elif isinstance(rev, (int, float)) and rev > 0 and any(abs(a / rev - 1) < 0.005 for a in amounts_usd_mn(q)):
        return {"vendor": name, "status": "sourced", "revenue_usd_mn": float(rev), "quote": quote,
                "url": src.get("url"), "domain": src.get("domain")}
    else:
        why = "the share (or revenue) is not stated in its quote"
    return {"vendor": name, "status": "rejected", "why": why, "share_pct": share}


def check_shares(rows: list[dict], snippets: list[dict], market: str | None, year: int) -> dict:
    """{"verified": [...], "rejected": [...], "unsourced": [{"vendor", "share_pct"}]}."""
    out = {"verified": [], "rejected": [], "unsourced": []}
    for r in rows or []:
        name = r.get("name") or r.get("vendor")
        if not isinstance(r, dict) or not name or is_other(name):
            continue
        v = verify_share(r, snippets, market, year)
        if v["status"] == "sourced":
            out["verified"].append(v)
        elif v["status"] == "rejected":
            out["rejected"].append({"vendor": name, "why": v["why"], "share_pct": r.get("share_pct")})
        elif isinstance(r.get("share_pct"), (int, float)):
            out["unsourced"].append({"vendor": name, "share_pct": float(r["share_pct"])})
    return out


def share_reask_note(rejected: list[dict], what: str = "share_quote") -> str:
    return ("\n\nYOUR PREVIOUS ANSWER FAILED THE SOURCE CHECK for these company shares:\n"
            + "\n".join(f"- {r['vendor']}: {r['why']}" for r in rejected)
            + f"\nA {what} must be copied EXACTLY, character for character, from one WEB EVIDENCE line above; that "
              "line must name the company and this market and state the company's share (as a %) or its revenue "
              f"in this market. If no evidence line does, set {what} to \"\". Never invent a quote. Return the full "
              "JSON again.")


def check_shares_with_reask(data: dict, snippets: list[dict], ask: Callable[[str], dict], log: list[str],
                            market: str | None, year: int | None = None) -> tuple[dict, dict]:
    """The fleet competitive_landscape answer: shares checked against the
    snippets shown, re-asked once when a quoted share fails. Returns (answer,
    check); check["verified"] are the only global shares the report may print."""
    from AiAgentsFleet.cmi_report.model import VENDOR_YEAR
    year = year or VENDOR_YEAR
    rows = (data or {}).get("companies") or []
    check = check_shares(rows, snippets, market, year)
    check["reasked"] = False
    if check["rejected"]:
        log.append("[competitive_landscape] " + "; ".join(f"{r['vendor']}: {r['why']}" for r in check["rejected"])[:300]
                   + " -- re-asking once")
        first = check["rejected"]
        try:
            again = ask(share_reask_note(first))
        except Exception as exc:  # noqa: BLE001
            log.append(f"[competitive_landscape] re-ask failed: {exc}")
            again = None
        if isinstance(again, dict) and again.get("companies"):
            data = again
            check = check_shares(again["companies"], snippets, market, year)
        check["reasked"], check["first_rejected"] = True, first
    log.append(f"[competitive_landscape] {len(check['verified'])} company share(s) sourced from the evidence"
               + (f", {len(check['rejected'])} quote(s) rejected" if check["rejected"] else ""))
    check["year"] = year
    return data, check


# --------------------------------------------------------------------------- #
# the CMI vendors answer: checked and re-asked once
# --------------------------------------------------------------------------- #
def vendor_answer_problems(ans: dict, mea_check: dict) -> list[str]:
    """What is incoherent in one vendors answer: MEA share quotes that fail,
    and companies the answer itself says do not earn this market's revenue but
    still lists as MEA vendors."""
    probs = [f"MEA share of {r['vendor']}: {r['why']}" for r in mea_check.get("rejected") or []]
    out_of_market = [c.get("company") for c in ans.get("company_classification") or []
                     if isinstance(c, dict) and c.get("earns_defined_revenue") is False]
    listed = [x.get("vendor") for k in ("mea_shares", "positioning", "dashboard") for x in ans.get(k) or []
              if isinstance(x, dict)]
    for n in out_of_market:
        hit = next((v for v in listed if v and same_vendor(v, n)), None)
        if hit:
            probs.append(f"{hit} is listed as a vendor, but your classification says it does not earn the revenue "
                         f"this market measures")
    if not ans.get("positioning"):
        probs.append("the positioning list is empty")
    return list(dict.fromkeys(probs))


def review_vendor_answer(fields: dict, snippets: list[dict], market: str, ask: Callable[[str, str], dict],
                         log: list[str], year: int | None = None) -> dict:
    """Checks fields["vendors"]; re-asks once (`ask("vendors", note)`) when it
    is incoherent and keeps the new answer if it is usable. Returns the record
    kept as fields["vendor_review"]: {"problems", "reasked", "after",
    "mea_shares": the share check of the answer kept}."""
    from AiAgentsFleet.cmi_report.model import VENDOR_YEAR
    year = year or VENDOR_YEAR
    ans = fields.get("vendors")
    if not isinstance(ans, dict) or "error" in ans:
        return {"problems": [], "reasked": False, "skipped": "no vendors answer"}
    check = check_shares(ans.get("mea_shares"), snippets, market, year)
    probs = vendor_answer_problems(ans, check)
    rec: dict = {"problems": probs, "reasked": False}
    if probs:
        log.append("[vendors] " + "; ".join(probs)[:400] + " -- re-asking once")
        prev = {k: v for k, v in ans.items() if k != "_sources"}
        note = ("\n\nYOUR PREVIOUS ANSWER (below) failed these checks:\n" + "\n".join(f"- {p}" for p in probs)
                + "\nAn MEA share needs a quote copied EXACTLY from one WEB EVIDENCE line that names the company and "
                  "states its share; otherwise share_pct is null and quote is \"\". List only companies that earn the "
                  "revenue the DEFINITION measures. Return a corrected answer in exactly the same shape.\n"
                + f"PREVIOUS ANSWER:\n{json.dumps(prev, ensure_ascii=False)}")
        rec["reasked"] = True
        try:
            again = ask("vendors", note)
        except Exception as exc:  # noqa: BLE001
            log.append(f"[vendors] re-ask failed: {exc}")
            again = None
        if isinstance(again, dict) and "error" not in again and (again.get("positioning") or again.get("dashboard")):
            ans = again
            fields["vendors"] = again
            check = check_shares(ans.get("mea_shares"), snippets, market, year)
        else:
            rec["reask_failed"] = True
        rec["after"] = vendor_answer_problems(ans, check)
    check["year"] = year
    rec["mea_shares"] = check
    return rec


# --------------------------------------------------------------------------- #
# the market's product, as words a snippet must contain
# --------------------------------------------------------------------------- #
_PSTOP = {"and", "or", "of", "the", "for", "from", "in", "with", "global", "market", "other", "others", "its",
          "their", "all", "based", "products", "product", "output", "sales", "value", "revenue", "derivatives",
          "salts", "e", "g", "eg", "etc", "at", "level", "to", "by", "as", "a", "an", "under"}


def _content(text: str) -> list[str]:
    return [w for w in _simple(re.sub(r"\((?:e\.g\.?|i\.e\.?)[^)]*\)", " ", text or "")).split() if w not in _PSTOP]


def product_phrases(market: str, definition: dict | None = None) -> list[str]:
    """What a snippet must name for a company to count as selling THIS market's
    product: the market name ('vitamin c'), its last two words when it is
    longer ('shrimp farming', 'poultry feed'), and the 2-4 word product terms
    of the definition (included items, the measures' brackets: 'ascorbic acid',
    'sodium ascorbate'). Generic words ('vitamin', 'feed') alone never count."""
    root = re.sub(r"^\s*global\s+|\s+market\s*$", "", market or "", flags=re.I).strip()
    words = _content(root)
    out = [" ".join(words)] if words else []
    if len(words) >= 3:
        out.append(" ".join(words[-2:]))
    d = definition or {}
    terms = list(d.get("included_items") or [])
    for inner in re.findall(r"\(([^)]*)\)", str(d.get("measures") or "")):
        terms += re.split(r",|;|\band\b|\bor\b", inner)
    for t in terms:
        ws = _content(str(t))
        if 2 <= len(ws) <= 4:
            out.append(" ".join(ws))
    return list(dict.fromkeys(p for p in out if p))


def _stem(w: str) -> str:
    if len(w) > 5 and w.endswith("ing"):
        w = w[:-3]
    elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    return w[:6]


def mentions_product(text: str, phrases: list[str]) -> str | None:
    """The product phrase `text` names (every word of it, as a word start; a
    1-2 letter word -- the 'c' of 'vitamin c' -- right after the word before
    it), or None."""
    low = " " + _simple(text) + " "
    for ph in phrases:
        ws = ph.split()
        ok = bool(ws)
        for i, w in enumerate(ws):
            if len(w) <= 2:
                ok = i > 0 and bool(re.search(rf"\b{re.escape(_stem(ws[i - 1]))}\w*\s+{re.escape(w)}\b", low))
            else:
                ok = bool(re.search(rf"\b{re.escape(_stem(w))}", low))
            if not ok:
                break
        if ok:
            return ph
    return None


# a snippet ties a company to the product only when it says the company makes, supplies or sells it
_SUPPLY = re.compile(r"mak(?:e|es|er|ers|ing)|made|produc\w*|manufactur\w*|suppl(?:y|ies|ier|iers|ying)|sell\w*|"
                     r"sold|export\w*|offer\w*|farm(?:s|er|ers|ed)?|operat\w*|grow(?:s|er|ers|n)?|portfolio")
# a snippet that is only a publication about the product is not evidence that the company sells it
_MENTION_ONLY = re.compile(r"\b(guides?|guidance|articles?|webinars?|reports?|study|studies|blog|white ?papers?|"
                           r"podcasts?|interviews?)\b", re.I)
_NEAR = 12                                       # words between the supply word and the company / the product


def states_supply(text: str, who: list[str], phrase: str) -> bool:
    """`text` states that the company (any name in `who`) makes, produces,
    supplies or sells the product `phrase`: a supply word within _NEAR words of
    both the company and the product -- not merely the two in one text. A
    publication snippet ('guide', 'article', 'webinar', 'report') never counts."""
    if not text or _MENTION_ONLY.search(text):
        return False
    toks = _simple(text).split()

    def starts(seq: list[str]) -> list[int]:
        return [i for i in range(len(toks) - len(seq) + 1) if toks[i:i + len(seq)] == seq]
    at_name = [i for w in who if w for k in name_keys(w) for i in starts(k.split())]
    first = phrase.split()[0]
    at_prod = [i for i, t in enumerate(toks) if t.startswith(_stem(first))]
    at_verb = [i for i, t in enumerate(toks) if _SUPPLY.fullmatch(t)]
    return any(abs(v - n) <= _NEAR and abs(v - p) <= _NEAR for v in at_verb for n in at_name for p in at_prod)


# --------------------------------------------------------------------------- #
# entity facts (HQ, parent, business type, product)
# --------------------------------------------------------------------------- #
ENTITY_SYSTEM = (
    "You check basic facts about one company for a market report, using ONLY the WEB EVIDENCE given. Copy every "
    "quote EXACTLY, character for character, from one WEB EVIDENCE line; a fact no line states gets an empty "
    "quote. Never guess a headquarters country, a parent company or an ownership type. The headquarters is THIS "
    "named company's own; when the evidence gives only its parent's headquarters, name the parent in parent_group "
    "and leave hq_country empty. A joint venture is 'joint_venture', never 'wholly_owned'. A company sells this "
    "market's product only if an evidence line names it (or its division) together with the PRODUCT itself -- a "
    "broader category ('vitamins', 'animal feed') is not enough, and neither is a finished product that merely "
    "contains it (a multivitamin capsule is not vitamin C). earns_defined_revenue is false ONLY when the "
    "evidence shows the company does not make or sell this product at the definition's value-chain level (e.g. a "
    "retailer, a meat-only company, a processor in a farm-output market); a diversified company with a division "
    "that makes the product is true (name the division); if the evidence does not say, it is null. Return ONLY "
    "valid JSON.")
ENTITY_SCHEMA = (
    '{"name": <as given>, '
    '"same_company": <false if the evidence is about a different company with a similar name (e.g. a coal miner '
    'called New Hope when the vendor is a feed maker) or only about a foreign subsidiary of it>, '
    '"hq_country": <country of THIS company\'s own headquarters, or "">, '
    '"hq_quote": <evidence line stating the COUNTRY where THIS company (not its parent) is headquartered, or "">, '
    '"parent_group": <its ultimate parent company, or the company itself if independent>, '
    '"parent_quote": <evidence line stating the parent (not needed if independent), or "">, '
    '"ownership": "independent|wholly_owned|majority|joint_venture|minority|unknown", '
    '"business_type": <e.g. "producer", "manufacturer", "technology provider", "input supplier", "distributor", '
    '"integrator">, "business_quote": <evidence line describing what it does, or "">, '
    '"division": <the division that makes or sells the PRODUCT if the company is diversified, e.g. "Cargill Animal '
    'Nutrition"; "" otherwise>, '
    '"product_quote": <evidence line naming the company (or that division) together with the PRODUCT, or "">, '
    '"role": <one line: what it makes or sells in this market, only as product_quote states it, or "">, '
    '"earns_defined_revenue": <true|false|null, see the rules>, "earns_reason": <1 sentence>, '
    '"exclusion_quote": <when false: the evidence line showing it does not make or sell the product, else "">, '
    '"mainly_domestic": <true if it sells mainly in its home country>, '
    '"subsidiaries_in_list": [<companies from OTHER VENDORS it owns outright>], '
    '"ownership_note": <current ownership, merger or rebrand, e.g. "now part of X", or "">, '
    '"ownership_quote": <evidence line for it, or "">, '
    '"moves": [{"event": <8-14 words>, "year": <int>, "quote": <evidence line stating the move and its year>}, '
    '... up to 3 deals, acquisitions or expansions; [] if none is in the evidence]}')

_COUNTRY_ALIASES = {
    "United States": ["united states", "u s", "usa", "us based", "american"], "United Kingdom": ["united kingdom", "uk", "britain", "british"],
    "UAE": ["uae", "united arab emirates", "emirates", "dubai", "abu dhabi"], "Saudi Arabia": ["saudi"],
    "South Korea": ["south korea", "korea"], "Netherlands": ["netherlands", "dutch"], "China": ["china", "chinese"],
    "Vietnam": ["vietnam", "viet nam", "vietnamese"], "India": ["india", "indian"], "Japan": ["japan", "japanese"],
    "Germany": ["germany", "german"], "Switzerland": ["switzerland", "swiss"], "France": ["france", "french"],
    "Thailand": ["thailand", "thai"], "Israel": ["israel", "israeli"], "Turkey": ["turkey", "turkiye", "turkish"],
}
_ISO2 = {"CN": "China", "US": "United States", "USA": "United States", "UK": "United Kingdom", "GB": "United Kingdom",
         "DE": "Germany", "CH": "Switzerland", "IN": "India", "JP": "Japan", "VN": "Vietnam", "TH": "Thailand",
         "SA": "Saudi Arabia", "AE": "UAE", "NL": "Netherlands", "FR": "France", "IL": "Israel", "TR": "Turkey",
         "BR": "Brazil", "KR": "South Korea", "NO": "Norway", "IT": "Italy", "ES": "Spain", "EG": "Egypt",
         "ZA": "South Africa"}
# an exclusion reason that only says the company is broad, or that the evidence is silent, excludes nothing
_VAGUE = re.compile(r"\b(broad|diversified|conglomerate|holding company|not specifically|not solely|not primarily|"
                    r"no (?:web )?evidence|cannot be confirmed|does not (?:state|specify|mention|show)|unclear)\b", re.I)


def _mentions_country(text: str, country: str) -> bool:
    from AiAgentsFleet.cmi_report.groups import canonical
    c = canonical(country)
    low = " " + _simple(text) + " "
    return any(f" {_simple(a)} " in low for a in [c, country] + _COUNTRY_ALIASES.get(c, []))


def _names_other(text: str, name: str, other: str) -> bool:
    """`text` names `other` apart from `name` ('Japfa Comfeed, part of Japfa Ltd' names Japfa Ltd)."""
    low = " " + _simple(text) + " "
    for k in sorted(name_keys(name), key=len, reverse=True):
        low = low.replace(f" {k} ", " ")
    return any(f" {k} " in low for k in name_keys(other))


def verify_entity(raw: dict, name: str, snippets: list[dict], products: list[str] | None = None) -> dict:
    """The entity record. HQ, parent, business type and product each count only
    when an evidence line backs them:
    - the HQ quote states the country and is about THIS company -- a quote that
      names a (different) parent may give the parent's HQ, so it does not count;
    - the product quote names the company (or its division) with one of the
      market's product phrases (product_phrases) -- 'vitamin producer' is not
      'vitamin C';
    - an exclusion needs a quote too, and a reason that is more than 'broad
      company' or 'no evidence'; a company the evidence ties to the product is
      never excluded.
    `verified` needs HQ, parent and business type; `product_verified` the product."""
    from AiAgentsFleet.cmi_report.groups import canonical
    from AiAgentsFleet.cmi_report.verify_size import _find
    raw = raw if isinstance(raw, dict) else {}
    why = []
    if raw.get("same_company") is False:               # the evidence is about another company: nothing counts
        raw = {"name": name}
        why.append("the evidence describes a different company of a similar name")
    division = str(raw.get("division") or "").strip()

    def backed(quote_key: str, *must: str, who: tuple[str, ...] = (name,)) -> dict | None:
        q = str(raw.get(quote_key) or "")
        src = _find(q, snippets) if q else None
        return src if src and any(named_in(q, w) for w in who if w) and all(named_in(q, x) for x in must if x) else None

    parent = str(raw.get("parent_group") or "").strip()
    own = str(raw.get("ownership") or "unknown").strip().lower()
    self_owned = not parent or same_vendor(parent, name) or own == "independent"
    parent_ok = self_owned or bool(backed("parent_quote", parent))
    if not parent_ok:
        why.append(f"parent '{parent}' not stated in the evidence")
    hq = str(raw.get("hq_country") or "").strip()
    hq = canonical(_ISO2.get(hq.upper(), hq)) if hq else ""          # 'CN' -> 'China'
    hq_src = backed("hq_quote") if hq else None
    hq_ok = bool(hq_src) and _mentions_country(raw.get("hq_quote", ""), hq)
    if hq_ok and not self_owned and _names_other(raw.get("hq_quote", ""), name, parent):
        hq_ok = False
        why.append(f"the HQ quote is about its parent {parent}, not {name} itself")
    elif not hq_ok:
        why.append("HQ not stated in the evidence" if hq else "no HQ given")
    jv = re.search(r"joint[- ]venture|\bJV\b", str(raw.get("parent_quote") or ""), re.I)
    wholly = (not self_owned and parent_ok and own == "wholly_owned" and not jv)
    btype = str(raw.get("business_type") or "").strip()
    biz_ok = bool(btype) and bool(backed("business_quote"))
    if not biz_ok:
        why.append("business type not described in the evidence")
    pq = str(raw.get("product_quote") or "")
    phrase = mentions_product(pq, products or []) if products else None
    role = str(raw.get("role") or "").strip()
    prod_ok = bool(phrase) and bool(role) and bool(backed("product_quote", who=(name, division)))
    if prod_ok and not states_supply(pq, [name, division], phrase):
        prod_ok = False
        why.append("the product evidence line does not say it makes, supplies or sells the product")
    elif not prod_ok:
        why.append("no evidence line names it with this market's product" if products else "product not checked")
    earns = raw.get("earns_defined_revenue")
    reason = str(raw.get("earns_reason") or "").strip()
    excluded = None
    if prod_ok:
        earns = True                                    # tied to the product by the evidence: never excluded
    elif earns is False and backed("exclusion_quote") and reason and not _VAGUE.search(reason):
        excluded = reason
    else:
        earns = None                                    # not shown either way: unknown, not excluded
    note = str(raw.get("ownership_note") or "").strip()
    note_ok = bool(note) and bool(backed("ownership_quote"))
    moves = []
    for mv in raw.get("moves") or []:
        if not isinstance(mv, dict):
            continue
        q, y = str(mv.get("quote") or ""), mv.get("year")
        if isinstance(y, int) and str(y) in q and _find(q, snippets) and named_in(q, name):
            moves.append({"event": mv.get("event"), "year": y, "quote": q})
    return {
        "name": name,
        "hq_country": hq if hq_ok else "", "hq_verified": hq_ok,
        "parent_group": (name if self_owned else parent) if parent_ok else "", "parent_is_self": self_owned,
        "parent_verified": parent_ok, "ownership": "independent" if self_owned else own, "wholly_owned": wholly,
        "business_type": btype if biz_ok else "", "business_verified": biz_ok,
        "division": division if prod_ok and division and _simple(division) != _simple(name) else "",
        "product_verified": prod_ok, "product_phrase": phrase if prod_ok else None,
        "product_quote": pq if prod_ok else "", "role": role if prod_ok else "",
        "earns_defined_revenue": earns, "excluded_why": excluded,
        "exclusion_quote": str(raw.get("exclusion_quote") or "") if excluded else "",
        "mainly_domestic": bool(raw.get("mainly_domestic")),
        "subsidiaries_in_list": [s for s in raw.get("subsidiaries_in_list") or [] if isinstance(s, str)],
        "ownership_note": note if note_ok else "", "ownership_note_claimed": note,
        "moves": moves,
        "evidence_url": (hq_src or {}).get("url"),
        "verified": hq_ok and parent_ok and biz_ok, "why": why,
    }


def entity_queries(name: str, market: str, products: list[str] | None = None) -> list[str]:
    """HQ, parent, the market name and -- for the product check -- the shortest
    other product term ('CSPC ascorbic acid', 'Cargill poultry feed')."""
    root = re.sub(r"^\s*global\s+|\s+market\s*$", "", market or "", flags=re.I)
    qs = [f"{name} company headquarters", f"{name} parent company subsidiary owned by", f"{name} {root}"]
    other = sorted((p for p in products or [] if _simple(p) != _simple(root)), key=len)
    return qs + ([f"{name} {other[0]}"] if other else [])


def check_entity(name: str, market: str, definition_text: str, claims: dict, others: list[str], llm,
                 search: Callable[[list[str]], list[dict]], log: list[str], products: list[str] | None = None) -> dict:
    """One targeted search and one LLM call for one vendor. Only snippets that
    name the company are shown (a degraded search backend returns unrelated
    pages); the search is retried once when fewer than 2 do."""
    shown: list[dict] = []
    for attempt in range(2):
        try:
            hits = [s for s in search(entity_queries(name, market, products)) if named_in(s.get("text", ""), name)]
        except Exception as exc:  # noqa: BLE001
            log.append(f"[entities] search failed for {name}: {exc}")
            hits = []
        if len(hits) > len(shown):
            shown = hits
        if len(shown) >= 2 or attempt:
            break
        time.sleep(15)
    shown = shown[:12]
    claimed = {k: v for k, v in (claims or {}).items() if v}
    user = (f"Market: {market}\n\n{definition_text}"
            + (f"PRODUCT (an evidence line must name the company with one of these): {'; '.join(products)}\n"
               if products else "")
            + f"COMPANY: {name}\n"
            + (f"CLAIMED (check each against the evidence; correct or leave out): {json.dumps(claimed, ensure_ascii=False)}\n"
               if claimed else "")
            + f"OTHER VENDORS: {', '.join(o for o in others if o != name)}\n\n"
            + ("WEB EVIDENCE:\n" + "\n".join(f"- {s['text']}" for s in shown) if shown else "(no web evidence found)")
            + f"\n\nReturn ONLY valid JSON matching exactly this shape:\n{ENTITY_SCHEMA}")
    failed = None
    try:
        raw = llm.complete_json(ENTITY_SYSTEM, user)
    except Exception as exc:  # noqa: BLE001
        log.append(f"[entities] LLM failed for {name}: {exc}")
        raw, failed = {}, str(exc)[:120]
    rec = verify_entity(raw, name, shown, products)
    if failed:                                    # unverified because the check could not run, not for lack of evidence
        rec["why"] = [f"entity check failed ({failed})"]
    rec["snippets"] = len(shown)
    return rec


def universe(fleet_fields: dict, vendors: dict | None) -> list[str]:
    """Every vendor the report could name, in priority order: the fleet's
    global list, the positioning list, the MEA share list, the dashboard."""
    names: list[str] = []
    rows = [c.get("name") for c in (fleet_fields.get("competitive_landscape") or {}).get("companies") or []]
    for k in ("positioning", "mea_shares", "dashboard"):
        rows += [x.get("vendor") for x in (vendors or {}).get(k) or [] if isinstance(x, dict)]
    for n in rows:
        if n and not is_other(n) and not any(same_vendor(n, x) for x in names):
            names.append(str(n))
    return names


def claims_for(name: str, vendors: dict | None) -> dict:
    """What the vendors agent said about a company (checked by the entity call)."""
    v = vendors or {}
    dash = next((r for r in v.get("dashboard") or [] if isinstance(r, dict) and same_vendor(r.get("vendor", ""), name)), {})
    cls = next((r for r in v.get("company_classification") or []
                if isinstance(r, dict) and same_vendor(r.get("company", ""), name)), {})
    return {"hq": dash.get("hq"), "parent_group": cls.get("parent_group"), "business_type": cls.get("business_type"),
            "ownership_note": dash.get("ownership_note")}


def check_entities(names: list[str], market: str, definition_text: str, vendors: dict | None, llm,
                   search: Callable[[list[str]], list[dict]], log: list[str], cap: int = VENDOR_CAP,
                   products: list[str] | None = None) -> dict:
    """fields["entities"]: {"records": {name: record}, "not_checked": [names beyond the cap]}."""
    records = {}
    for n in names[:cap]:
        records[n] = check_entity(n, market, definition_text, claims_for(n, vendors), names, llm, search, log,
                                  products)
    log.append(f"[entities] {len(records)} vendor(s) checked, {len(included(names, records))} confirmed as making or selling the product"
               + (f"; {len(names) - cap} beyond the cap of {cap} not checked" if len(names) > cap else ""))
    return {"records": records, "not_checked": names[cap:], "products": products or []}


def included(names: list[str], records: dict) -> list[str]:
    """The vendors the report can print (user decision 2026-10-15): the evidence says the company makes or
    sells this product, and it is not excluded. HQ, parent and business type are printed only when confirmed
    ("Not confirmed" otherwise) -- they no longer decide whether the vendor is listed."""
    return [n for n in names if (r := lookup(n, records)) and r.get("product_verified")
            and not r.get("excluded_why")]


# --------------------------------------------------------------------------- #
# a thin vendor list is asked for once more
# --------------------------------------------------------------------------- #
MIN_VENDORS = 5                      # fewer confirmed vendors: the list is asked for once more
MIN_TABLE_VENDORS = 3                # fewer: no vendor table at all, one sentence instead
UNCONFIRMED = "Vendor-level data for this market could not be confirmed from public sources."
THIN_QUERIES = ["{market} leading manufacturers producers", "{market} key players list",
                "{market} largest companies by capacity"]


def concentrated_in(records: dict) -> str | None:
    """The one country most confirmed HQs are in (at least 3 and a third of them),
    for a country-specific search -- e.g. China for vitamin C."""
    hqs = [r["hq_country"] for r in records.values() if r.get("hq_verified") and r.get("hq_country")
           and not r.get("excluded_why")]
    if len(hqs) < 3:
        return None
    top = max(set(hqs), key=hqs.count)
    return top if hqs.count(top) >= 3 and hqs.count(top) * 3 >= len(hqs) else None


THIN_SYSTEM = (
    "You name companies for a market report. List ONLY companies that earn EXACTLY the revenue the DEFINITION "
    "measures, at its value-chain level -- e.g. for a farm-output market, companies that farm (not processors, "
    "exporters, feed or equipment suppliers); for a manufacturer market, companies that make the product (not "
    "retailers, brands of finished goods or traders). Never list a company on the EXCLUDE list. Prefer companies "
    "named in the WEB EVIDENCE; never invent a company. Return ONLY valid JSON.")
THIN_SCHEMA = ('{"companies": [{"vendor": <company name>, "role": <one line: what it makes or sells in this '
               'market>}, ... up to 10 companies NOT on the EXCLUDE list]}')


def thin_prompt(definition: dict, records: dict, n_included: int) -> str:
    """The body of the dedicated thin-list re-ask (THIN_SYSTEM): the definition,
    the companies already checked as an EXCLUDE list and why companies were
    excluded -- and nothing else (no top-companies context, no instruction to
    position every company, which made the agent repeat the same names)."""
    excl = [f"- {n}: {r['excluded_why']}" for n, r in records.items() if r.get("excluded_why")]
    return (f"Only {n_included} company(ies) could be confirmed as earning the revenue this market measures.\n"
            f"- Value-chain level: {definition.get('value_chain_level') or '(not stated)'}\n"
            f"- Measures: {definition.get('measures') or definition.get('definition') or '(not stated)'}\n"
            f"EXCLUDE (already checked -- do not list any of these): {'; '.join(records)}\n"
            + ("Companies excluded, with the reason (do not list companies like them):\n" + "\n".join(excl) + "\n"
               if excl else "")
            + "Name OTHER companies that earn exactly that revenue.\n"
            + f"Return ONLY valid JSON matching exactly this shape:\n{THIN_SCHEMA}")


def merge_new(vendors: dict, companies: list[dict]) -> dict:
    """The vendors answer with the re-ask's new companies added to positioning."""
    out = dict(vendors or {})
    rows = [x for x in out.get("positioning") or [] if isinstance(x, dict)]
    rows += [{"vendor": str(c["vendor"]), "role": str(c.get("role") or "")} for c in companies
             if isinstance(c, dict) and c.get("vendor") and not is_other(c["vendor"])
             and not any(same_vendor(c["vendor"], y.get("vendor", "")) for y in rows)]
    out["positioning"] = rows
    return out


def reask_if_thin(fields: dict, fleet_fields: dict, definition: dict, market: str, ask: Callable, check_new: Callable,
                  log: list[str]) -> dict:
    """Fewer than MIN_VENDORS confirmed vendors: one dedicated re-ask
    (`ask(THIN_SYSTEM, thin_prompt(...), queries) -> {"companies": [...]}`) on
    extra searches (THIN_QUERIES and one country query when the confirmed HQs
    are concentrated in one country). The new companies -- any that is already
    checked is dropped -- get their own entity checks (`check_new(names) ->
    {name: record}`). Returns the record kept in fields["vendor_review"]["thin_reask"]."""
    ents = fields.get("entities") or {}
    records = dict(ents.get("records") or {})
    names = universe(fleet_fields, fields.get("vendors"))
    before = included(names, records)
    rec: dict = {"included_before": before, "reasked": False}
    if len(before) >= MIN_VENDORS:
        return rec
    country = concentrated_in(records)
    queries = THIN_QUERIES + ([f"{{market}} {country} manufacturers producers companies"] if country else [])
    log.append(f"[vendors] only {len(before)} confirmed vendor(s) -- re-asking once"
               + (f" (with a {country} search)" if country else ""))
    rec.update({"reasked": True, "queries": queries, "country": country, "exclude": list(records),
                "excluded_reasons": {n: r["excluded_why"] for n, r in records.items() if r.get("excluded_why")}})
    try:
        again = ask(THIN_SYSTEM, thin_prompt(definition, records, len(before)), queries)
    except Exception as exc:  # noqa: BLE001
        log.append(f"[vendors] thin re-ask failed: {exc}")
        again = None
    companies = (again or {}).get("companies") if isinstance(again, dict) else None
    if not companies:
        rec["reask_failed"] = True
        return rec
    new = [str(c["vendor"]) for c in companies if isinstance(c, dict) and c.get("vendor")
           and not is_other(c["vendor"]) and lookup(str(c["vendor"]), records) is None]
    rec["repeated"] = [str(c.get("vendor")) for c in companies if isinstance(c, dict)
                       and lookup(str(c.get("vendor")), records) is not None]
    fields["vendors"] = merge_new(fields.get("vendors") or {}, [c for c in companies if str(c.get("vendor")) in new])
    records.update(check_new(new[:VENDOR_CAP]))
    names = universe(fleet_fields, fields["vendors"])
    fields["entities"] = {**ents, "records": records,
                          "not_checked": [n for n in names if lookup(n, records) is None]}
    rec["new_vendors"] = new
    rec["included_after"] = included(names, records)
    log.append(f"[vendors] after the re-ask: {len(new)} new compan(ies), {len(rec['included_after'])} confirmed vendor(s)")
    return rec


# --------------------------------------------------------------------------- #
# one exclusion list, plausibility
# --------------------------------------------------------------------------- #
def exclusions(records: dict) -> dict[str, str]:
    """{company: why} for every company the evidence shows does not make or sell
    this market's product (entity check, verify_entity). Applied to the global
    and MEA lists alike. A company the evidence is silent on is not excluded --
    it is simply not printed until confirmed."""
    return {n: (r.get("business_type") or "other business") + f": {r['excluded_why']}"
            for n, r in (records or {}).items() if r.get("excluded_why")}


def plausibility(rows: list[dict], market_value: float, home_value: Callable[[str], float | None],
                 records: dict) -> list[dict]:
    """Problems with shares: {"vendor", "kind", "detail"}. rows: {"vendor",
    "share_pct", "sourced"}. A mainly-domestic vendor whose implied revenue
    (share x market) exceeds its home country's market is impossible; an
    unsourced share above 20% is blocked."""
    out = []
    for r in rows:
        n, s = r["vendor"], float(r.get("share_pct") or 0)
        rec = lookup(n, records) or {}
        home = rec.get("hq_country") if rec.get("hq_verified") else None
        hv = home_value(home) if home else None
        implied = s / 100 * market_value
        if rec.get("mainly_domestic") and hv and implied > hv:
            out.append({"vendor": n, "kind": "over_home",
                        "detail": f"{n}'s implied revenue (${implied:,.1f}M at {s:g}%) exceeds the whole {home} market "
                                  f"(${hv:,.1f}M), yet it sells mainly at home"})
        if not r.get("sourced") and s > UNSOURCED_MAX_PCT:
            out.append({"vendor": n, "kind": "unsourced_over_20",
                        "detail": f"{n} at {s:g}% has no source and is above {UNSOURCED_MAX_PCT:g}%"})
    return out


# --------------------------------------------------------------------------- #
# m["vendor_review"]
# --------------------------------------------------------------------------- #
def review(*, fleet_check: dict | None, answer_record: dict | None, entities: dict | None, has_vendors: bool,
           excluded: dict[str, str], dropped: list[str], merges: list[str], declined: list[str],
           blocked_sourced: list[dict], blocked_unsourced: list[dict], mode: dict, mea_leader: str | None,
           positioning_rows: int) -> dict:
    """Every vendor check and its outcome. Blockers need a human before
    release (PLAN-08 gates on them); warnings record what was left out."""
    blockers, warnings = [], []
    if has_vendors and not entities:
        blockers.append("vendor entities not checked (data predates PLAN-05): HQ, parent and business type are "
                        "unverified -- re-run run_data --groups vendors")
    elif entities and any("product_verified" not in r for r in (entities.get("records") or {}).values()):
        blockers.append("vendor entity checks predate the product check: no vendor is confirmed as selling this "
                        "market's product -- re-run run_data --groups entities")
    if fleet_check is None:
        warnings.append("global vendor shares not checked (fleet run predates PLAN-05): no global share is printed")
    for b in blocked_sourced:
        blockers.append(f"implausible sourced share: {b['detail']} -- not printed")
    for b in blocked_unsourced:
        warnings.append(f"blocked share: {b['detail']} -- not printed")
    rec = answer_record or {}
    for p in rec.get("after") if rec.get("reasked") else []:
        warnings.append(f"vendors answer: {p} (kept after one re-ask; handled by the exclusion list / share check)")
    for n, why in excluded.items():
        warnings.append(f"excluded from every vendor list: {n} ({why})")
    warnings += [f"left out of the vendor tables: {d}" for d in dropped]
    warnings += [f"not merged: {d}" for d in declined]
    thin = rec.get("thin_reask") or {}
    if not mode.get("shares_global") and not mode.get("shares_mea") and positioning_rows < MIN_VENDORS:
        warnings.append(f"only {positioning_rows} vendor(s) confirmed as making or selling the product"
                        + (" after one re-ask of the vendor list" if thin.get("reasked") else ""))
    if not mea_leader:
        warnings.append("no MEA vendor leader identified")
    return {"mode": mode, "mea_leader": mea_leader, "excluded": excluded, "merges": merges,
            "global_share_check": fleet_check, "vendor_answer": rec,
            "entities_checked": len((entities or {}).get("records") or {}),
            "entities_not_checked": (entities or {}).get("not_checked") or [],
            "blockers": blockers, "warnings": warnings}
