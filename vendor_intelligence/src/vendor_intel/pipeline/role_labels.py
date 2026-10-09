"""Lightweight market-role labelling, run inside the pipeline.

ONE vocabulary call + batched assignment over the exported companies (batches run concurrently,
bounded by ROLE_LABEL_CONCURRENT, default 8). Gives each
company a CLEAR market-specific role (e.g. 'Re-Refiner', 'Base-Oil Offtaker', 'Antenna OEM') and
a FUNCTIONALITY phrase describing how it is aligned to / serves the market. Also flags clearly
off-market companies so they can be dropped.

Deliberately minimal (~10-15 calls/run).
"""
from __future__ import annotations

import json
from typing import Any

HAIKU = "claude-haiku-4-5-20251001"

_VOCAB_SYS = (
    "You are a market-structure analyst. Given a MARKET and its value-chain SECTIONS, list the "
    "distinct ROLE labels companies play in THIS market. Each label is 1-3 words, specific to the "
    "market (e.g. waste oil: 'Used-Oil Collector', 'Re-Refiner', 'Base-Oil Offtaker'; SATCOM: "
    "'Antenna OEM', 'Capacity Operator', 'Service Integrator'). Cover every section. "
    'Return ONLY JSON: {"roles":[{"label":"...","definition":"..."}]}'
)
_ASSIGN_SYS = (
    "For each company, using its name, the MARKET, and the provided NOTES, produce three fields:\n"
    "1. summary: 3-4 factual sentences about the company - what it is, what it makes/does, its "
    "products or operations, and how it relates to THIS market. No marketing words ('leading', "
    "'innovative'), no invented specifics; if unsure, stay general but accurate.\n"
    "2. role: ONE clear market-specific role label (1-3 words) from the VOCABULARY. The role MUST be "
    "CONSISTENT with the company's SECTION: in a Distributors/Traders section use a distribution role "
    "(e.g. 'Bulk Distributor', 'Ingredient Supplier', 'Trader'); in a Suppliers/Raw-Materials section "
    "use a supply role (e.g. 'Raw Avocado Supplier', 'Feedstock Supplier'); in a Manufacturers/"
    "Producers section use a production role (e.g. 'Oil Processor', 'Producer'). NEVER label a company "
    "in a distributor or supplier section as a 'Processor'.\n"
    "3. detail: a FUNCTIONALITY phrase (max 12 words) DERIVED FROM the summary, describing how the "
    "company is aligned to / serves this market - its specific activity in the value chain. Factual, "
    "no company name, no marketing words, no trailing period.\n"
    "If the company clearly does NOT belong in this market, set role to 'Off-Market' and detail to a "
    "5-word reason. "
    'Return ONLY JSON: {"items":[{"i":<index>,"summary":"<3-4 sentences>","role":"<label>","detail":"<phrase>"}]}'
)

_OFFMARKET_ROLE_PREFIXES = (
    "off-market", "offmarket", "excluded", "unclassified", "unrelated", "non-market",
    "not applicable", "out of scope", "not relevant", "unknown", "n/a",
)
_OFFMARKET_DETAIL_SIGNALS = (
    "cannot be confirmed", "does not produce", "outside core", "insufficient", "unclear",
    "not involved", "no market presence", "does not participate", "not a participant",
    "no accessible company information", "not core ", "does not manufacture",
    "no active presence", "not a satcom", "no documented",
)


def is_excluded_segment(r: dict) -> bool:
    """True when the role pass flagged this company as belonging to a brief-excluded segment
    (a sizing-scope exclusion, distinct from generic off-market junk)."""
    return str(r.get("market_role") or "").strip().lower().startswith("excluded")


def is_offmarket(r: dict) -> bool:
    mr = str(r.get("market_role") or "").strip().lower()
    det = str(r.get("market_role_detail") or "").strip().lower()
    if any(mr.startswith(w) for w in _OFFMARKET_ROLE_PREFIXES):
        return True
    if "off-market" in mr or "offmarket" in mr or "off-market" in det or "offmarket" in det:
        return True
    if det in ("unknown", "n/a", "none"):
        return True
    return any(k in det for k in _OFFMARKET_DETAIL_SIGNALS)


def _jsonl(items: list[dict]) -> str:
    return "\n".join(json.dumps(p, ensure_ascii=False) for p in items)


_UNV_SYS = (
    "For each company (name + website domain) write ONE factual sentence describing what the "
    "company most likely is and does in the stated MARKET, inferred from its name and domain "
    "(e.g. a Spanish/Portuguese 'aceite'/'avocado' name suggests an avocado/edible-oil producer; "
    "an '...exports'/'...enterprise' name suggests a trader/supplier). Be cautious and do NOT invent "
    "specifics. End the sentence with ' (website could not be retrieved; inferred from public "
    "signals).' "
    'Return ONLY JSON: {"items":[{"i":<index>,"description":"<one sentence>"}]}'
)


def _parallel_llm(client: Any, system: str, users: list[str], *, max_tokens: int, what: str) -> list[Any]:
    """Run independent batch LLM calls CONCURRENTLY (bounded by ROLE_LABEL_CONCURRENT, default 8).
    Results are returned in input order; a failed batch yields {} (logged), like the old loop."""
    import os
    from concurrent.futures import ThreadPoolExecutor

    if not users:
        return []
    try:
        workers = max(1, int(os.getenv("ROLE_LABEL_CONCURRENT", "8") or "8"))
    except ValueError:
        workers = 8

    def _one(user: str) -> Any:
        try:
            return client.complete_json(system, user, model=HAIKU, max_tokens=max_tokens)
        except Exception as e:
            print(f"  [roles] {what} batch failed: {e}", flush=True)
            return {}

    with ThreadPoolExecutor(max_workers=min(workers, len(users))) as ex:
        return list(ex.map(_one, users))


def describe_unverified(unverified: list[dict], query_context: dict, settings: Any, client: Any) -> int:
    """Give each Not-Verified company a short LLM-inferred description (its site couldn't be
    fetched). Writes into 'reason'. Returns count described. Best-effort; never raises."""
    rows = [u for u in (unverified or []) if str(u.get("company") or "").strip()]
    if not rows or not getattr(client, "available", False):
        return 0
    market = str(query_context.get("industry") or "")
    done = 0
    batches = [rows[s : s + 10] for s in range(0, len(rows), 10)]
    users = [
        f"MARKET: {market}\n\nCOMPANIES:\n"
        + _jsonl([{"i": i, "name": u.get("company"), "domain": u.get("domain") or ""} for i, u in enumerate(b)])
        for b in batches
    ]
    outs = _parallel_llm(client, _UNV_SYS, users, max_tokens=2048, what="unverified-describe")
    for batch, out in zip(batches, outs):
        arr = out.get("items") if isinstance(out, dict) else out
        by = {int(x["i"]): str(x.get("description") or "") for x in (arr or []) if "i" in x}
        for i, u in enumerate(batch):
            d = by.get(i, "").strip()
            if d:
                u["reason"] = d
                done += 1
    return done


_UNV_PLACE_SYS = (
    "For each company (name + website domain) use your knowledge and the name/domain to decide "
    "whether it genuinely operates in the MARKET, and if so which SECTION it best fits.\n"
    "- relevant: false ONLY if it clearly does NOT belong in this market (then omit the other fields).\n"
    "- section: the EXACT text of the best-fitting section from the numbered list.\n"
    "- role: a 1-3 word market role CONSISTENT with that section.\n"
    "- detail: a functionality phrase (max 12 words) describing its activity in the market - no "
    "company name, no marketing words, no trailing period.\n"
    "- summary: 2-3 factual sentences about the company inferred CAUTIOUSLY from its name and domain; "
    "do NOT invent specific products, figures, or claims - stay general but accurate.\n"
    'Return ONLY JSON: {"items":[{"i":<index>,"relevant":<bool>,"section":"<exact>","role":"<label>",'
    '"detail":"<phrase>","summary":"<2-3 sentences>"}]}'
)


def place_unverified_in_sections(
    unverified: list[dict], relevant: list[dict], query_context: dict, settings: Any, client: Any
) -> list[dict]:
    """Classify each not-yet-verified company (name + domain only) into the best-fitting market
    SECTION via the LLM and return full export rows for the relevant ones, so they appear inline
    with the rest instead of in a separate 'Not Verified' section. Best-effort; never raises."""
    from vendor_intel.pipeline.sections import match_custom_section

    rows = [u for u in (unverified or []) if str(u.get("company") or "").strip()]
    if not rows or not getattr(client, "available", False):
        return []
    market = str(query_context.get("industry") or "")
    sections = [name for name, _ in _grouped([c for c in relevant if c.get("is_relevant")], query_context)]
    if not sections:
        sections = [str(s).strip() for s in (query_context.get("sections") or []) if str(s).strip()]
    if not sections:
        return []
    block = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(sections))
    placed: list[dict] = []
    batches = [rows[s : s + 8] for s in range(0, len(rows), 8)]
    users = [
        f"MARKET: {market}\n\nSECTIONS:\n{block}\n\nCOMPANIES:\n"
        + _jsonl([{"i": i, "name": u.get("company"), "domain": u.get("domain") or ""} for i, u in enumerate(b)])
        for b in batches
    ]
    outs = _parallel_llm(client, _UNV_PLACE_SYS, users, max_tokens=2560, what="unverified placement")
    for batch, out in zip(batches, outs):
        arr = out.get("items") if isinstance(out, dict) else out
        by = {int(x["i"]): x for x in (arr or []) if "i" in x}
        for i, u in enumerate(batch):
            x = by.get(i)
            if not x or not x.get("relevant"):
                continue
            canon = match_custom_section(str(x.get("section") or ""), sections)
            if not canon:
                continue
            dom = str(u.get("domain") or "").strip()
            placed.append(
                {
                    "company": u.get("company") or "",
                    "brand": "",
                    "domain": dom,
                    "website": (f"https://{dom}" if dom else ""),
                    "industry": market,
                    "country": str(query_context.get("country") or ""),
                    "is_relevant": True,
                    "role": str(x.get("role") or ""),
                    "market_role": str(x.get("role") or ""),
                    "market_role_detail": str(x.get("detail") or ""),
                    "company_summary": str(x.get("summary") or ""),
                    "_forced_section": canon,
                    "_inferred_profile": True,
                    "confidence": 0.6,
                }
            )
    return placed


def _grouped(rows: list[dict], query_context: dict):
    from vendor_intel.pipeline.sections import (
        build_section_taxonomy,
        group_into_sections,
        main_product_label,
    )

    scope = query_context.get("scope") if isinstance(query_context.get("scope"), dict) else None
    mp = main_product_label(query_context, scope)
    custom = [str(s).strip() for s in (query_context.get("sections") or []) if str(s).strip()]
    if custom:
        return group_into_sections(rows, custom, mp, custom=True)
    return group_into_sections(rows, build_section_taxonomy(mp), mp)


_MULTI_SYS = (
    "You are a value-chain analyst. Given a MARKET, numbered SECTIONS, and a company (name + "
    "summary), identify EVERY section the company genuinely operates in. MOST operate in exactly "
    "ONE - assign MULTIPLE only with clear evidence it performs activities in each (e.g. a "
    "vertically-integrated firm that both produces and distributes). For each, give a 1-3 word role "
    "label. Use the EXACT section text provided. "
    'Return ONLY JSON: {"items":[{"i":<index>,"segments":[{"section":"<exact section>","role":"<label>"}]}]}'
)


def detect_multi_segments(
    relevant: list[dict], query_context: dict, settings: Any, client: Any
) -> int:
    """Tag companies that operate in 2+ sections with `multi_segments` = [{section, role}].
    Returns the count of multi-segment players. Best-effort; never raises."""
    from vendor_intel.pipeline.sections import match_custom_section

    rows = [c for c in (relevant or []) if c.get("is_relevant")]
    if not rows or not getattr(client, "available", False):
        return 0
    market = str(query_context.get("industry") or "")
    sections = [name for name, _ in _grouped(rows, query_context)]
    if len(sections) < 2:
        return 0
    block = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(sections))
    multi = 0
    items = [
        {"i": i, "name": c.get("company") or c.get("brand") or "",
         "summary": str(c.get("company_summary") or c.get("role_description") or "")[:500], "_row": c}
        for i, c in enumerate(rows)
    ]
    batches = [items[s : s + 8] for s in range(0, len(items), 8)]
    users = [
        f"MARKET: {market}\n\nSECTIONS:\n{block}\n\nCOMPANIES:\n"
        + _jsonl([{k: it[k] for k in ("i", "name", "summary")} for it in b])
        for b in batches
    ]
    outs = _parallel_llm(client, _MULTI_SYS, users, max_tokens=2048, what="multi-segment")
    for batch, out in zip(batches, outs):
        if not out:
            continue
        arr = out.get("items") if isinstance(out, dict) else out
        by = {int(x["i"]): (x.get("segments") or []) for x in (arr or []) if "i" in x}
        for it in batch:
            clean, seen = [], set()
            for seg in by.get(it["i"], []):
                canon = match_custom_section(str(seg.get("section") or ""), sections)
                role = str(seg.get("role") or "").strip()
                if canon and role and canon not in seen:
                    seen.add(canon)
                    clean.append({"section": canon, "role": role})
            it["_row"]["multi_segments"] = clean
            if len(clean) >= 2:
                multi += 1
            elif len(clean) == 1 and not str(it["_row"].get("_forced_section") or "").strip():
                # single-section company: place it in the LLM-determined section (more accurate
                # than keyword routing, so a re-refiner lands in Re-Refining, not Manufacturers).
                # Seeds keep their pre-assigned _forced_section.
                it["_row"]["_forced_section"] = clean[0]["section"]
    return multi


def assign_market_roles(
    relevant: list[dict], query_context: dict, settings: Any, client: Any
) -> int:
    """Set market_role + market_role_detail on each exported company. Returns count labelled.
    Best-effort; never raises."""
    rows = [c for c in (relevant or []) if c.get("is_relevant")]
    if not rows or not getattr(client, "available", False):
        return 0
    market = str(query_context.get("industry") or "")
    grouped = _grouped(rows, query_context)
    sections = [name for name, _ in grouped]
    exclude = [str(s).strip() for s in (query_context.get("exclude_segments") or []) if str(s).strip()]
    exclude_note = (
        "\nOUT-OF-SCOPE SEGMENTS (excluded from this market's sizing scope by the analyst): "
        + "; ".join(exclude)
        + ". If a company PRIMARILY belongs to an out-of-scope segment, set role to "
        "'Excluded-Segment' and detail to a 5-word reason naming the segment.\n"
        if exclude
        else ""
    )

    vocab = ""
    try:
        user = f"MARKET: {market}\nSECTIONS:\n" + "\n".join(f"- {s}" for s in sections)
        out = client.complete_json(_VOCAB_SYS, user, model=HAIKU, max_tokens=1024)
        roles = out.get("roles") if isinstance(out, dict) else out
        vocab = "\n".join(
            f"- {r.get('label')}: {r.get('definition', '')}" for r in (roles or []) if r.get("label")
        )
    except Exception as e:
        print(f"  [roles] vocabulary step failed: {e}", flush=True)

    done = 0
    # Build every (section, batch) job first, then run all assignment calls concurrently.
    jobs: list[list[dict]] = []
    users: list[str] = []
    for section, srows in grouped:
        items = [
            {
                "i": i,
                "name": c.get("company") or c.get("brand") or "",
                "coarse_role": c.get("role") or "",
                "summary": str(c.get("company_summary") or c.get("role_description") or "")[:500],
                "_row": c,
            }
            for i, c in enumerate(srows)
        ]
        for s in range(0, len(items), 8):
            batch = items[s : s + 8]
            payload = [{k: it[k] for k in ("i", "name", "coarse_role", "summary")} for it in batch]
            jobs.append(batch)
            users.append(
                f"MARKET: {market}\nSECTION: {section}{exclude_note}\n\nVOCABULARY:\n{vocab}\n\n"
                f"COMPANIES:\n{_jsonl(payload)}"
            )
    outs = _parallel_llm(client, _ASSIGN_SYS, users, max_tokens=2560, what="assign")
    for batch, out in zip(jobs, outs):
        arr = out.get("items") if isinstance(out, dict) else out
        by = {int(x["i"]): x for x in (arr or []) if isinstance(x, dict) and "i" in x}
        for it in batch:
            x = by.get(it["i"])
            if x and str(x.get("role") or "").strip():
                it["_row"]["market_role"] = str(x["role"]).strip()
                it["_row"]["market_role_detail"] = str(x.get("detail") or "").strip()
                summ = str(x.get("summary") or "").strip()
                if summ:
                    it["_row"]["company_summary"] = summ
                done += 1
    return done
