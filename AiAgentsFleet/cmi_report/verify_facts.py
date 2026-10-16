"""Claim-by-claim verification of the regulatory facts and vendor moves a
report prints (PLAN-06, FINDINGS X11).

The `regulation` agent's own `verified` flag is a self-report, so it is not
used. Instead every printed fact becomes an atomic claim
`{"id", "kind", "country", "text", "year"}` that is checked on its own:

  1. 1-2 targeted web searches built from the claim, junk sources dropped
     (`sources.filter_sources`);
  2. a SEPARATE judge call (its own system prompt; its own model when
     DEAGENT_VERIFY_MODEL is set) returns supported | contradicted | renamed |
     not_found, with a quote, and a corrected value when there is one;
  3. the verdict is then checked against the evidence itself: the quote must
     be in a snippet, and the claim (supported) or the correction (renamed /
     contradicted) must be stated in that quote. Anything else is not_found.

Applying the results (`apply`, used by model.build and run_cmi_report):
  supported -> printed as is; renamed / contradicted with a backed correction
  -> the corrected value is printed (logged in `fact_review.corrections`);
  anything else -> omitted. The regulator table keeps every country row, with
  "Competent authority not confirmed" in the regulator cell.

Outcomes: cmi_fields.json `fields.fact_checks` (the results),
output/<slug>/fact_checks.json (results + what is printed + review) and
m["fact_review"] (blockers / warnings for the release gate, PLAN-08).

Nothing is corrected from hard-coded knowledge: every correction comes from
evidence found at run time.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from AiAgentsFleet.cmi_report.guard import sentences, unverified_fact

VERSION = 5          # 5: evidence from the Perplexity Search API; 4: judge must tie the authority to the category
MAX_CLAIMS = 25                      # cost cap per report; the rest are not checked (and so not printed)
NOT_CONFIRMED = "Competent authority not confirmed"
REQ_NOT_CONFIRMED = "Requirements not confirmed"
VERDICTS = ("supported", "contradicted", "renamed", "not_found")
_MIN_QUOTE = 20
# where a claim is printed: lower = more visible, checked first (tables > timeline > profiles > other)
_VISIBILITY = {"regulator": 0, "policy": 1, "milestone": 2, "vendor_move": 3}

JUDGE_SYSTEM = (
    "You are an independent fact-checker. You did not write the CLAIM and have no stake in it. Judge it ONLY "
    "against the numbered EVIDENCE snippets -- never from memory. Verdicts:\n"
    "- supported: a snippet states the claim as written (the same body or instrument, the same year).\n"
    "- renamed: a snippet shows the body named in the claim now has another name, or its function moved to "
    "another body; corrected = the CURRENT name, as the snippet gives it.\n"
    "- contradicted: a snippet states a different fact (another year, another instrument or authority, or the "
    "event did not happen as described); corrected = the fact as the snippet states it, \"\" if it gives none.\n"
    "- not_found: no snippet confirms or contradicts the claim.\n"
    "A year is part of the claim: a different year is 'contradicted'. A proposal is not an entry into force. "
    "quote = words copied EXACTLY from ONE snippet (at least one full clause) that show your verdict; "
    "evidence = that snippet's number. Return ONLY valid JSON."
)
_JUDGE_SHAPE = ('{"verdict": "supported|contradicted|renamed|not_found", "corrected": <string, "" if none>, '
                '"corrected_year": <integer or null>, "evidence": <snippet number or null>, '
                '"quote": <exact words from that snippet, "" for not_found>, "reason": <1 sentence>}')
_CORRECTION_HINT = {
    "regulator": "corrected = the full current name of the competent authority for this product in this country. "
                 "The quote must show that this authority registers, licenses or regulates the PRODUCT or its "
                 "CATEGORY (below) -- prefer the snippet that says so (e.g. an official registration page). A "
                 "quote that only describes the body in general ('X is a ministry responsible for agriculture') "
                 "is not enough: answer not_found.",
    "milestone": "corrected = the event in 8-14 words with the instrument's correct name; corrected_year = the year "
                 "it entered into force.",
    "law": "corrected = the instrument's correct name and number; corrected_year = its correct year.",
    "policy": "corrected = the requirement rewritten as the snippet states it (1 sentence).",
    "date": "corrected_year = the correct year.",
    "vendor_move": "corrected = the company's MEA presence or move rewritten as the snippet states it (1 sentence).",
}

_STOP = {"the", "and", "for", "with", "from", "that", "this", "into", "under", "over", "its", "their", "are", "was",
         "were", "has", "have", "been", "being", "will", "which", "must", "also", "such", "other", "than", "more",
         "most", "each", "all", "any", "per", "via", "within", "across", "including", "requires", "required",
         "require", "products", "product", "market", "markets", "national", "new", "now"}
_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_ACRONYM = re.compile(r"\b[A-Z][A-Z&]{1,7}\b")


# --------------------------------------------------------------------------- #
# text helpers
# --------------------------------------------------------------------------- #
def _norm(s: str) -> str:
    s = (s or "").replace("’", "'").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9%'/-]+", " ", s.lower())).strip()


def _words(s: str) -> set[str]:
    """Content words (singular, lower case) of a phrase."""
    out = set()
    for w in re.findall(r"[a-z]+", _norm(s)):
        if len(w) < 4 or w in _STOP:
            continue
        out.add(w[:-1] if w.endswith("s") and not w.endswith("ss") else w)
    return out


def _years(s: str) -> set[str]:
    return set(_YEAR.findall(s or ""))


def _acronyms(s: str) -> set[str]:
    return {a for a in _ACRONYM.findall(s or "") if len(a) >= 2}


def product_name(market: str) -> str:
    """'Global Vitamin C Market' -> 'Vitamin C'."""
    return re.sub(r"^\s*global\s+|\s+market\s*$", "", market or "", flags=re.I).strip() or market


def _name_backed(name: str, quote: str, share: float = 0.6) -> bool:
    """A body or instrument name is stated in `quote`: its acronym is there, or
    most of its content words are."""
    if any(a in quote for a in _acronyms(name) if len(a) >= 3):
        return True
    w = _words(re.sub(r"\([^)]*\)", " ", name))
    return bool(w) and len(w & _words(quote)) / len(w) >= share


# --------------------------------------------------------------------------- #
# 1. claims
# --------------------------------------------------------------------------- #
def _canonical(c: str) -> str:
    from AiAgentsFleet.cmi_report.groups import canonical
    return canonical(c)


def claims(fields: dict) -> list[dict]:
    """Every regulatory fact and vendor move the report would print, as atomic
    claims, ordered by visibility and capped at MAX_CLAIMS by `verify`. The
    product is not part of a claim's text (the judge is told it separately)."""
    reg = fields.get("regulation") or {}
    out: list[dict] = []
    for r in reg.get("snapshot") or []:
        country = _canonical(str(r.get("country") or "").strip())
        if not country:
            continue
        if str(r.get("regulator") or "").strip():
            out.append({"id": f"regulator:{country}", "kind": "regulator", "country": country,
                        "subject": r["regulator"].strip(),
                        "tie_terms": [str(t) for t in reg.get("category_terms") or [] if str(t).strip()],
                        "text": f"{r['regulator'].strip()} is the competent authority in {country}."})
        for j, s in enumerate(sentences(str(r.get("requirements") or ""))[:3]):
            out.append({"id": f"requirement:{country}:{j}", "kind": "policy", "country": country, "text": s.strip(),
                        "subject": r.get("regulator") or ""})
    for i, ms in enumerate(reg.get("milestones") or []):
        ev = str(ms.get("event") or "").strip()
        if not ev:
            continue
        date = str(ms.get("date") or "").strip()
        inst = str(ms.get("instrument") or "").strip()
        yrs = _years(date)
        out.append({"id": f"milestone:{i}", "kind": "milestone", "country": str(ms.get("jurisdiction") or ""),
                    "subject": inst or ev, "year": int(min(yrs)) if yrs else None,
                    "text": f"{date}: {ms.get('jurisdiction', '')} -- {ev}" + (f" ({inst})" if inst else "")})
    for i, d in enumerate((fields.get("vendors") or {}).get("dashboard") or []):
        fp = str(d.get("mea_footprint") or "").strip()
        if fp:
            out.append({"id": f"vendor:{i}", "kind": "vendor_move", "country": "", "subject": str(d.get("vendor") or ""),
                        "text": f"{d.get('vendor', '')}: {fp}"})
    order = {c["id"]: k for k, c in enumerate(out)}
    return sorted(out, key=lambda c: (_VISIBILITY.get(c["kind"], 9), order[c["id"]]))


def queries(c: dict, product: str) -> list[str]:
    """1-2 targeted searches for one claim."""
    if c["kind"] == "regulator":
        # the category the regulation agent named ('fertiliser') finds pages that tie the agency to the
        # product; a product-name-only query mostly finds generic 'this ministry exists' pages
        cat = next((t for t in c.get("tie_terms") or [] if t.strip()), "")
        if cat:
            return [f"{c['subject']} {c['country']} {cat} registration",
                    f"{c['country']} {cat} registration competent authority {product}"]
        return [f"{c['subject']} {c['country']} registration {product}",
                f"{c['country']} {product} regulator registration authority"]
    if c["kind"] == "policy":
        return [f"{c['country']} {product} registration requirements {c.get('subject') or ''}".strip()]
    if c["kind"] in ("milestone", "law", "date"):
        body = re.sub(r"^[^:]*:\s*", "", c["text"])
        return [f"{c['subject']} {c.get('year') or ''}".strip(), f"{c['country']} {body}"[:150]]
    return [c["text"][:150]]


# --------------------------------------------------------------------------- #
# 2. judging one claim
# --------------------------------------------------------------------------- #
def judge_prompt(c: dict, snippets: list[dict], product: str = "") -> str:
    ev = "\n".join(f"[{k + 1}] ({s.get('domain') or s.get('url') or 'unknown'}) {s.get('text', '')}"
                   for k, s in enumerate(snippets))
    cat = ", ".join(c.get("tie_terms") or [])
    return ((f"PRODUCT: {product}\n" if product else "") + (f"CATEGORY: {cat}\n" if cat else "") + f"CLAIM ({c['kind']}{', ' + c['country'] if c.get('country') else ''}): {c['text']}\n\n"
            f"EVIDENCE:\n{ev or '(none)'}\n\n{_CORRECTION_HINT.get(c['kind'], '')}\n\n"
            f"Return ONLY JSON of exactly this shape:\n{_JUDGE_SHAPE}")


def _source_of(quote: str, snippets: list[dict]) -> dict | None:
    q = _norm(quote)
    if len(q) < _MIN_QUOTE:
        return None
    return next((s for s in snippets if q in _norm(s.get("text", ""))), None)


_RENAMED = re.compile(r"\b(replac\w*|formerly|renamed|succeed\w*|supersed\w*|abolish\w*|dissolv\w*|merged|"
                      r"split|no longer|predecessor|previously|restructur\w*)\b", re.I)
_PLACE_ACRONYMS = {"UAE", "USA", "KSA", "GCC", "MEA", "MENA"}


def _claim_backed(c: dict, quote: str) -> bool:
    """The quote states the claim: every year and acronym of the claim, and its
    body, instrument or most of its wording -- and does not say the body was
    replaced or renamed (that would be a judge calling a rename 'supported')."""
    body = c["subject"] if c["kind"] == "regulator" else re.sub(r"^[^:]*:\s*", "", c["text"])
    if not _years(c["text"]) <= _years(quote):
        return False
    if any(a not in quote for a in _acronyms(body) - _PLACE_ACRONYMS if len(a) >= 3):
        return False
    if _RENAMED.search(quote):
        return False
    if c["kind"] == "regulator":
        return _name_backed(c["subject"], quote)
    w = _words(body)
    return bool(w) and len(w & _words(quote)) / len(w) >= 0.6


def _correction_backed(c: dict, corrected: str, year: int | None, quote: str) -> bool:
    if year is not None and str(year) not in quote:
        return False
    if not corrected:
        return year is not None
    if not _years(corrected) <= _years(quote):
        return False
    return _name_backed(corrected, quote, 0.6 if c["kind"] == "regulator" else 0.4)


def validate(c: dict, raw: dict, snippets: list[dict]) -> dict:
    """The judge's answer, kept only where the evidence itself backs it."""
    raw = raw if isinstance(raw, dict) else {}
    verdict = str(raw.get("verdict") or "").strip().lower().replace(" ", "_")
    verdict = verdict if verdict in VERDICTS else "not_found"
    quote = str(raw.get("quote") or "").strip()
    corrected = str(raw.get("corrected") or "").strip()
    try:
        cyear = int(raw["corrected_year"]) if raw.get("corrected_year") not in (None, "") else None
    except (TypeError, ValueError):
        cyear = None
    out = {"verdict": verdict, "judge_verdict": verdict, "status": "omitted", "corrected": "", "corrected_year": None,
           "quote": "", "url": "", "domain": "", "reason": str(raw.get("reason") or "")[:300], "note": ""}
    if verdict == "not_found":
        out["note"] = "no evidence confirms the claim"
        return out
    src = _source_of(quote, snippets)
    if not src:
        out.update(verdict="not_found", note="the judge's quote is not in any evidence snippet")
        return out
    out.update(quote=quote, url=src.get("url") or "", domain=src.get("domain") or "")
    if verdict == "supported":
        if _claim_backed(c, quote):
            out["status"] = "supported"
        else:
            out.update(verdict="not_found", note="the quote does not state the claim as written")
        return out
    # renamed / contradicted: printed only with a correction the quote states
    if (corrected or cyear is not None) and _norm(corrected) != _norm(c.get("subject") or c["text"]) \
            and _correction_backed(c, corrected, cyear, quote):
        out.update(status="corrected", corrected=corrected, corrected_year=cyear)
    else:
        out["note"] = "contradicted, with no correction stated in the evidence" if verdict == "contradicted" \
            else "renamed, but the new name is not stated in the quote"
    return out


# --------------------------------------------------------------------------- #
# 3. running the checks
# --------------------------------------------------------------------------- #
def default_search(qs: list[str]) -> list[dict]:
    """Live web search, junk sources dropped (PLAN-01). Not used by the tests."""
    from me_engine.generation.web_evidence import gather_with_sources
    from AiAgentsFleet.sources import filter_sources
    kept, _ = filter_sources(gather_with_sources(qs, per_query=5, cap=10, precise=True))   # standard search
    return kept


def judge_llm(cfg, cache_path: Path | None):
    """The judge: a GenLLM on DEAGENT_VERIFY_MODEL when set (a different model
    from the writer), else the configured model with its own system prompt."""
    from me_engine.generation.llm import GenLLM
    model = os.environ.get("DEAGENT_VERIFY_MODEL")
    return GenLLM(dataclasses.replace(cfg, model=model) if model else cfg, cache_path=cache_path)


def _cache_key(c: dict) -> str:
    h = hashlib.sha256(f"{VERSION}\x00{c['kind']}\x00{c.get('country', '')}\x00{c['text']}".encode("utf-8"))
    return "verify_facts:" + h.hexdigest()


def _cache_get(llm, c: dict) -> dict | None:
    get = getattr(llm, "_cache_get", None)
    if not callable(get):
        return None
    try:
        v = get(_cache_key(c))
        return json.loads(v) if v else None
    except Exception:  # noqa: BLE001 - a broken cache entry is just a miss
        return None


def _cache_put(llm, c: dict, rec: dict) -> None:
    put = getattr(llm, "_cache_put", None)
    if callable(put):
        try:
            put(_cache_key(c), json.dumps(rec, ensure_ascii=False))
        except Exception:  # noqa: BLE001
            pass


def check_claim(c: dict, product: str, llm, search_fn) -> dict:
    """One claim: search, judge, validate. Cached by claim text in the shared
    GenLLM cache (a failed search or LLM call is not cached)."""
    hit = _cache_get(llm, c)
    if hit:
        if c["kind"] == "regulator" and hit.get("status") in ("supported", "corrected")                 and not product_tied(hit.get("quote") or "", product, c.get("tie_terms")):
            hit = {**hit, "verdict": "not_found", "status": "omitted",
                   "note": f"the quote does not tie this authority to {product}"}
        return {**c, **hit, "cached": True}
    try:
        # the first (most specific) query; the second only when the first finds nothing -- halves the
        # searches per claim (cost) without losing claims that the first query already answers
        qs = queries(c, product)
        snippets = search_fn(qs[:1]) or []
        if not snippets and len(qs) > 1:
            snippets = search_fn(qs[1:2]) or []
    except Exception as exc:  # noqa: BLE001
        return {**c, "verdict": "not_found", "status": "error", "note": f"search failed: {exc}"}
    if not snippets:
        return {**c, "verdict": "not_found", "status": "omitted", "note": "no evidence found"}
    try:
        raw = llm.complete_json(JUDGE_SYSTEM, judge_prompt(c, snippets, product))
    except Exception as exc:  # noqa: BLE001
        return {**c, "verdict": "not_found", "status": "error", "note": f"judge failed: {exc}"}
    res = validate(c, raw, snippets)
    if c["kind"] == "regulator" and res.get("status") in ("supported", "corrected")             and not product_tied(res.get("quote") or "", product, c.get("tie_terms")):
        # a quote that only shows the body exists (NAFDAC 'regulates food and drugs') does not make it the
        # authority for THIS product (user-approved fix, 2026-10-15 biostimulants run)
        res = {**res, "verdict": "not_found", "status": "omitted",
               "note": f"the quote does not tie this authority to {product}"}
    _cache_put(llm, c, res)
    return {**c, **res}


# broad sector words: a regulator quote must name the product itself, not just the sector
_BROAD = {"agricul", "farming", "industr", "commerc", "intensi", "super", "functio", "global", "market"}


def product_tied(quote: str, product: str, category_terms: list[str] | None = None) -> bool:
    """The quote names this product or its regulatory category ('fertiliser' for biostimulants, 'food
    supplement' for vitamin C, as the regulation agent named it), so the authority is tied to it -- not
    merely shown to exist."""
    from AiAgentsFleet.sources import market_tokens
    stems = (market_tokens(product) - _BROAD or market_tokens(product))
    for t in category_terms or []:
        stems |= {w[:7] for w in t.lower().replace("fertilizer", "fertiliser").split() if len(w) >= 4}
    q = quote.lower().replace("fertilizer", "fertiliser")
    return not stems or any(s in q for s in stems)


def verify(claim_list: list[dict], market: str, llm, search_fn=None, cap: int = MAX_CLAIMS,
           log=None) -> list[dict]:
    search_fn = search_fn or default_search
    product = product_name(market)
    out = []
    for k, c in enumerate(claim_list):
        if k >= cap:
            out.append({**c, "verdict": "not_found", "status": "not_checked",
                        "note": f"beyond the {cap}-claim cap; not printed"})
            continue
        out.append(check_claim(c, product, llm, search_fn))
    if log:
        n = {s: sum(1 for r in out if r["status"] == s) for s in ("supported", "corrected", "omitted", "not_checked",
                                                                  "error")}
        log(f"  [fact_checks] {len(out)} claim(s): " + ", ".join(f"{v} {k}" for k, v in n.items() if v))
    return out


def current(fields: dict) -> bool:
    """The stored checks cover exactly the claims the data now makes (no error)."""
    fc = fields.get("fact_checks") or {}
    have = {r["id"]: r for r in fc.get("results") or []}
    return bool(fc) and fc.get("version") == VERSION and all(
        c["id"] in have and have[c["id"]].get("text") == c["text"] and have[c["id"]].get("status") != "error"
        for c in claims(fields))


def ensure(market_dir: Path, market: str, fields: dict, llm, search_fn=None, log=print) -> bool:
    """Runs the checks the data needs (results already stored for an unchanged
    claim are reused), stores them in fields["fact_checks"] and writes
    fact_checks.json. Returns True when fields changed (save cmi_fields.json)."""
    changed = False
    if not current(fields):
        cl = claims(fields)
        old = {r["id"]: r for r in (fields.get("fact_checks") or {}).get("results") or []
               if (fields.get("fact_checks") or {}).get("version") == VERSION}
        todo = [c for c in cl if not (c["id"] in old and old[c["id"]].get("text") == c["text"]
                                      and old[c["id"]].get("status") not in ("error", "not_checked"))]
        done = {r["id"]: r for r in verify(todo, market, llm, search_fn,
                                           cap=max(0, MAX_CLAIMS - (len(cl) - len(todo))), log=log)}
        fields["fact_checks"] = {"version": VERSION, "checked_at": datetime.now(timezone.utc).isoformat(),
                                 "results": [done.get(c["id"]) or old[c["id"]] for c in cl]}
        changed = True
    write(market_dir, market, fields)
    return changed


# --------------------------------------------------------------------------- #
# 4. applying the results
# --------------------------------------------------------------------------- #
def _results(fields: dict) -> dict[str, dict]:
    fc = fields.get("fact_checks") or {}
    if fc.get("version") != VERSION:
        return {}
    return {r["id"]: r for r in fc.get("results") or []}


def _ok(res: dict | None, text: str) -> dict | None:
    """The check of a claim with this exact text, when it may be printed."""
    if res and res.get("text") == text and res.get("status") in ("supported", "corrected"):
        return res
    return None


def apply(fields: dict) -> dict:
    """A copy of the agent fields with every unconfirmed regulator, requirement,
    milestone and vendor move removed and every backed correction applied.
    Without checks (verification never ran) nothing unconfirmed is printed."""
    res = _results(fields)
    by_text = {c["id"]: c for c in claims(fields)}
    out = dict(fields)
    reg = dict(fields.get("regulation") or {})
    if reg:
        rows = []
        for r in reg.get("snapshot") or []:
            country = _canonical(str(r.get("country") or "").strip())
            row = {k: v for k, v in r.items() if k != "instrument"}     # not a checked claim: never printed
            cid = f"regulator:{country}"
            ok = _ok(res.get(cid), (by_text.get(cid) or {}).get("text", ""))
            row["regulator"] = (ok["corrected"] if ok and ok["status"] == "corrected" and ok.get("corrected")
                                else str(r.get("regulator") or "").strip() if ok else NOT_CONFIRMED)
            row["verified"] = bool(ok)
            kept = []
            for j, s in enumerate(sentences(str(r.get("requirements") or ""))[:3]):
                cid = f"requirement:{country}:{j}"
                ok_r = _ok(res.get(cid), (by_text.get(cid) or {}).get("text", ""))
                if ok_r:
                    kept.append(ok_r["corrected"] if ok_r["status"] == "corrected" and ok_r.get("corrected")
                                else s.strip())
            row["requirements"] = " ".join(kept) or REQ_NOT_CONFIRMED
            if row["verified"]:          # user decision 2026-10-15: unconfirmed authorities are left out
                rows.append(row)
        reg["snapshot_unconfirmed"] = [_canonical(str(r.get("country") or "").strip())
                                       for r in reg.get("snapshot") or []
                                       if _canonical(str(r.get("country") or "").strip())
                                       not in {x["country"] for x in rows}]
        reg["snapshot"] = rows
        ms_out = []
        for i, ms in enumerate(reg.get("milestones") or []):
            cid = f"milestone:{i}"
            ok = _ok(res.get(cid), (by_text.get(cid) or {}).get("text", ""))
            if not ok:
                continue
            ms = {**ms, "verified": True}
            if ok["status"] == "corrected":
                if ok.get("corrected_year"):
                    ms["date"] = str(ok["corrected_year"])
                if ok.get("corrected"):
                    ms["event"] = ok["corrected"]
            ms_out.append(ms)
        reg["milestones"] = sorted(ms_out, key=lambda x: min(_years(str(x.get("date"))) or {"9999"}))
        verified = verified_facts({**out, "regulation": reg})
        reg["watchlist"] = [w for w in reg.get("watchlist") or []
                            if not unverified_fact(" ".join(str(v) for v in w.values()), verified)]
        out["regulation"] = reg
    ven = fields.get("vendors")
    if isinstance(ven, dict) and ven.get("dashboard"):
        rows = []
        for i, d in enumerate(ven["dashboard"]):
            cid = f"vendor:{i}"
            ok = _ok(res.get(cid), (by_text.get(cid) or {}).get("text", ""))
            if not ok:          # an unconfirmed move is left out with its row (the table prints no empty cell)
                continue
            fp = ok["corrected"] if ok["status"] == "corrected" and ok.get("corrected") else d["mea_footprint"]
            rows.append({**d, "mea_footprint": fp, "verified": True})
        out["vendors"] = {**ven, "dashboard": rows}
    return out


def verified_facts(applied: dict) -> list[str]:
    """What the report may name: the confirmed (or corrected) regulators,
    requirements, milestones and vendor moves, as printed."""
    reg = applied.get("regulation") or {}
    out = []
    for r in reg.get("snapshot") or []:
        if r.get("regulator") and r["regulator"] != NOT_CONFIRMED:
            out.append(f"{r.get('country')}: {r['regulator']}")
        if r.get("requirements") and r["requirements"] != REQ_NOT_CONFIRMED:
            out.append(f"{r.get('country')}: {r['requirements']}")
    for ms in reg.get("milestones") or []:
        out.append(f"{ms.get('date')} {ms.get('jurisdiction', '')}: {ms.get('event', '')}"
                   + (f" ({ms['instrument']})" if ms.get("instrument") else ""))
    for d in (applied.get("vendors") or {}).get("dashboard") or []:
        if d.get("mea_footprint"):
            out.append(f"{d.get('vendor')}: {d['mea_footprint']}")
    return out


def review(fields: dict) -> dict:
    """m["fact_review"]: counts, corrections, omissions, the verified fact list
    and the blockers / warnings the release gate (PLAN-08) reads."""
    cl = claims(fields)
    res = _results(fields)
    applied = apply(fields)
    rows = [res.get(c["id"]) if (res.get(c["id"]) or {}).get("text") == c["text"] else None for c in cl]
    count = {s: sum(1 for r in rows if r and r.get("status") == s)
             for s in ("supported", "corrected", "omitted", "not_checked", "error")}
    missing = sum(1 for r in rows if r is None)
    blockers, warnings = [], []
    if cl and not res:
        blockers.append("fact verification has not run: every regulator, milestone and vendor move is left out")
    elif missing:
        blockers.append(f"{missing} claim(s) changed since the fact check and were left out -- re-run verification")
    if count["error"]:
        blockers.append(f"{count['error']} claim(s) could not be checked (search or judge failure) and were left out")
    corrections = [{"id": r["id"], "original": r["text"], "printed": r.get("corrected") or r.get("corrected_year"),
                    "verdict": r.get("verdict"), "quote": r.get("quote"), "url": r.get("url")}
                   for r in rows if r and r.get("status") == "corrected"]
    if corrections:
        warnings.append(f"{len(corrections)} claim(s) printed with a correction from the evidence")
    omitted = [{"id": c["id"], "text": c["text"], "why": (r or {}).get("note") or "not checked"}
               for c, r in zip(cl, rows) if not (r and r.get("status") in ("supported", "corrected"))]
    if omitted:
        warnings.append(f"{len(omitted)} unconfirmed claim(s) left out of the report")
    if count["not_checked"]:
        warnings.append(f"{count['not_checked']} claim(s) beyond the {MAX_CLAIMS}-claim cap were not checked")
    reg = applied.get("regulation") or {}
    no_auth = list(reg.get("snapshot_unconfirmed") or [])
    if no_auth:
        warnings.append("competent authority not confirmed (country left out of the regulatory table): "
                        + ", ".join(no_auth))
    raw_ms = len((fields.get("regulation") or {}).get("milestones") or [])
    if raw_ms >= 3 > len(reg.get("milestones") or []):
        warnings.append(f"only {len(reg.get('milestones') or [])} milestone(s) confirmed: the timeline is left out")
    return {"status": "checked" if res else "not_run", "claims": len(cl), **count, "changed_since_check": missing,
            "corrections": corrections, "omitted": omitted, "verified_facts": verified_facts(applied),
            "blockers": blockers, "warnings": warnings}


def printed(applied: dict) -> dict:
    """Every regulator, milestone and vendor move exactly as the report prints it."""
    reg = applied.get("regulation") or {}
    return {"regulators": [{"country": r.get("country"), "regulator": r.get("regulator"),
                            "requirements": r.get("requirements"), "confirmed": bool(r.get("verified"))}
                           for r in reg.get("snapshot") or []],
            "milestones": [{"date": m.get("date"), "jurisdiction": m.get("jurisdiction"), "event": m.get("event")}
                           for m in reg.get("milestones") or []],
            "vendor_moves": [{"vendor": d.get("vendor"), "mea_footprint": d.get("mea_footprint")}
                             for d in (applied.get("vendors") or {}).get("dashboard") or [] if d.get("mea_footprint")]}


def write(market_dir: Path, market: str, fields: dict) -> Path:
    out = Path(market_dir) / "fact_checks.json"
    fc = fields.get("fact_checks") or {}
    out.write_text(json.dumps({"market": market, "version": VERSION, "checked_at": fc.get("checked_at"),
                               "results": fc.get("results") or [], "printed": printed(apply(fields)),
                               "review": review(fields)}, indent=2, ensure_ascii=False), encoding="utf-8")
    return out
