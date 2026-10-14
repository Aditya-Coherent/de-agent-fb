"""Agent node functions for the synthetic Input-Sheet generator.

Every agent emits *judgements* (shares, growth rates, prices) grounded in DB +
web evidence. Deterministic helpers normalise vectors and expand base vectors to
per-country columns. A brain agent attaches justification artifacts. A reward
agent scores the number set and drives the refinement loop.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import replace
from statistics import median
from typing import Any

from . import segment_definition
from .cited_values import CAGR_RE, collect_cited_values, estimate_size_range, market_tokens, outside_size_range
from .db_source import (
    DBEvidence,
    fetch_market_evidence,
    fetch_market_evidence_semantic,
    verify_market_match,
)
from .llm import GenLLM
from .quality import compute_demand_quality_flags
from .skeleton import Skeleton
from .scope import (bottom_up as bottom_up_scope, brief as scope_brief, cmi_report_snippets,
                    figures_in as scope_figures, product_term, reconcile as reconcile_scope)
from .web_evidence import fetch_page_text, gather, gather_with_sources


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _normalize(vector: dict[str, float]) -> dict[str, float]:
    clean = {k: max(0.0, float(v)) for k, v in vector.items()}
    total = sum(clean.values())
    if total <= 0:
        n = len(clean) or 1
        return {k: round(1.0 / n, 4) for k in clean}
    out = {k: round(v / total, 4) for k, v in clean.items()}
    # fix rounding residue on the largest element
    residue = round(1.0 - sum(out.values()), 4)
    if out:
        top = max(out, key=out.__getitem__)
        out[top] = round(out[top] + residue, 4)
    return out


def _evidence_brief(state: dict[str, Any], dim: str | None = None) -> str:
    db: DBEvidence | None = state.get("db_evidence")
    parts: list[str] = []
    if db is not None:
        if db.raw_narrative:
            parts.append("DB NARRATIVE (market size / drivers):\n" + db.raw_narrative[:2200])
        if db.segmentation_tree:
            tree = "\n".join("  " * int(l.split("|", 1)[0]) + l.split("|", 1)[1] for l in db.segmentation_tree[:60])
            parts.append("DB SEGMENTATION TREE:\n" + tree)
    snippets = state.get("web_snippets") or []
    if snippets:
        parts.append("WEB EVIDENCE:\n" + "\n".join(f"- {s}" for s in snippets[:10]))
    scope_text = scope_brief(state.get("scope_reconciliation"))
    if scope_text:
        parts.append(scope_text)
    if dim is not None:
        dim_snippets = (state.get("dim_snippets") or {}).get(dim) or []
        if dim_snippets:
            parts.append(f"DIMENSION-SPECIFIC EVIDENCE ({dim}):\n" + "\n".join(f"- {s}" for s in dim_snippets))
    return "\n\n".join(parts) if parts else "(no external evidence available)"


def _evidence_numbered(state: dict[str, Any], dim: str | None = None) -> tuple[str, list[dict[str, str]]]:
    """The same evidence as `_evidence_brief`, numbered [E1], [E2], ... so a
    decision can cite exactly what it relied on; returns (text, items) and the
    items are kept in the run's trace."""
    items: list[dict[str, str]] = []
    db: DBEvidence | None = state.get("db_evidence")
    if db is not None and db.raw_narrative:
        items.append({"source": "CMI research database", "text": db.raw_narrative[:1500]})
    for s in (state.get("web_snippets") or [])[:10]:
        items.append({"source": "web search (market)", "text": s})
    scope_text = scope_brief(state.get("scope_reconciliation"))
    if scope_text:
        items.append({"source": "scope reconciliation (publisher consensus by end use)", "text": scope_text})
    if dim is not None:
        for s in (state.get("dim_snippets") or {}).get(dim) or []:
            items.append({"source": f"web search ({dim})", "text": s})
    for i, it in enumerate(items, start=1):
        it["id"] = f"E{i}"
    text = "\n".join(f"[{it['id']}] ({it['source']}) {it['text']}" for it in items) or "(no external evidence available)"
    return "EVIDENCE (cite by id):\n" + text, items


_UNIT_TO_MN = {"trillion": 1_000_000, "billion": 1_000, "million": 1, "bn": 1_000, "mn": 1}
_CITED_VALUE_RE = re.compile(
    r"(?:USD|US\$|\$)\s*([\d,]+(?:\.\d+)?)\s*(trillion|billion|million|bn|mn)\b", re.IGNORECASE
)


def _extract_cited_value_mn(cited_text: str) -> float | None:
    """Parse a real cited figure like 'USD 214 billion' into US$ Mn (214000),
    independent of whatever number the LLM itself reported -- LLMs are
    unreliable at billion/million conversion in their own arithmetic (verified:
    the same 'USD 214 billion' citation produced $214M in one pass and
    $214,000M in another for the identical market). This is the code-level
    check, not just a prompt instruction."""
    if not cited_text:
        return None
    m = _CITED_VALUE_RE.search(cited_text)
    if not m:
        return None
    number = float(m.group(1).replace(",", ""))
    return number * _UNIT_TO_MN[m.group(2).lower()]


_CAGR_RE = CAGR_RE
_VALUE_YEAR_RE = re.compile(
    r"(?:USD|US\$|\$)\s*([\d,]+(?:\.\d+)?)\s*(trillion|billion|million|bn|mn)\b[^.;]{0,25}?\b(?:in|by)\s+(20\d\d)",
    re.IGNORECASE,
)


def _extract_cited_value_for_year(cited_text: str, year: int) -> float | None:
    """US$ Mn value the citation states for `year` ('USD 13.61 billion in 2026')."""
    for v, unit, y in _VALUE_YEAR_RE.findall(cited_text or ""):
        if int(y) == year:
            return float(v.replace(",", "")) * _UNIT_TO_MN[unit.lower()]
    return None


def _extract_cited_cagr(cited_text: str) -> float | None:
    """The growth rate a cited figure states: an explicit 'CAGR of X%', or the
    rate implied by two dated values ('$26.5 billion in 2026 to $40.7 billion
    in 2033'). None when neither is present."""
    if not cited_text:
        return None
    m = _CAGR_RE.search(cited_text)
    if m:
        rate = float(m.group(1) or m.group(2)) / 100
        return rate if 0 < rate < 0.6 else None
    points = []
    for v, unit, year in _VALUE_YEAR_RE.findall(cited_text):
        points.append((int(year), float(v.replace(",", "")) * _UNIT_TO_MN[unit.lower()]))
    points = sorted(set(points))
    # The last two dated values span the forecast window ("from $26.5B in 2026
    # to $40.7B in 2033"); an earlier actual-year value is not part of it.
    if len(points) >= 2 and points[-1][0] > points[-2][0] and points[-2][1] > 0:
        (y0, v0), (y1, v1) = points[-2], points[-1]
        rate = (v1 / v0) ** (1 / (y1 - y0)) - 1
        return rate if 0 < rate < 0.6 else None
    return None


def _evidence_values_mn(state: dict[str, Any]) -> list[float]:
    """Every market-size figure ("USD 15.2 billion", "$900 million", ...) stated
    anywhere in the DB narrative or web snippets, in US$ Mn."""
    text = _evidence_brief(state)
    values = []
    for m in _CITED_VALUE_RE.finditer(text):
        try:
            values.append(float(m.group(1).replace(",", "")) * _UNIT_TO_MN[m.group(2).lower()])
        except ValueError:
            continue
    return [v for v in values if v > 0]


def _fix_unit_scale(anchor_mn: float, evidence_mn: list[float]) -> tuple[float, str]:
    """Catch a billions-vs-millions slip even when the LLM cited nothing.

    Compares the LLM's number with the median of every figure in the
    evidence. Only a gap of roughly 1000x (300x-3000x) is treated as a unit
    slip and rescaled; any smaller disagreement is a real estimation
    difference and is left alone.
    """
    if not evidence_mn or anchor_mn <= 0:
        return anchor_mn, ""
    ordered = sorted(evidence_mn)
    median = ordered[len(ordered) // 2]
    ratio = median / anchor_mn
    if 300 <= ratio <= 3000:
        return anchor_mn * 1000, f" [rescaled from ${anchor_mn:,.1f}M -- value was in billions; evidence median ${median:,.0f}M]"
    if 1 / 3000 <= ratio <= 1 / 300:
        return anchor_mn / 1000, f" [rescaled from ${anchor_mn:,.0f}M -- value was 1000x too large; evidence median ${median:,.0f}M]"
    return anchor_mn, ""


# --------------------------------------------------------------------------- #
# nodes
# --------------------------------------------------------------------------- #
def structure_node(state: dict[str, Any]) -> dict[str, Any]:
    cfg = state["config"]
    sk: Skeleton = state["skeleton"]
    llm: GenLLM = state["llm"]
    log = state.setdefault("log", [])
    definition = str(state.get("market_definition") or "")
    # With a written scope the DB and the web are searched for the scope's own
    # name ("Urea" for total urea), not the market label ("Urea Fertilizer").
    lookup = str(state.get("market_search_name") or sk.market_name)
    db_ev = None
    if cfg.neon_url:
        try:
            db_ev = fetch_market_evidence(cfg.neon_url, lookup)
        except Exception as exc:  # noqa: BLE001
            log.append(f"Could not reach the research database: {exc}")

        if db_ev is not None and cfg.is_online:
            try:
                if not verify_market_match(lookup, db_ev.raw_narrative, llm):
                    log.append(
                        f"Past research didn't match '{lookup}' closely enough -- "
                        "trying a meaning-based search instead of a keyword match"
                    )
                    db_ev = None
                    # A LIKE hit can land on a generic word (e.g. "retail") that
                    # matches an unrelated report's category tag. Before giving
                    # up, retry with a genuine meaning-based lookup.
                    try:
                        alt = fetch_market_evidence_semantic(cfg.neon_url, lookup)
                        if alt is not None and verify_market_match(lookup, alt.raw_narrative, llm):
                            db_ev = alt
                            log.append(f"Found a better match via meaning-based search: '{alt.keyword}'")
                        elif alt is not None:
                            log.append("No close match found in past research -- continuing without it")
                    except Exception as exc:  # noqa: BLE001
                        log.append(f"Meaning-based search failed: {exc}")
            except Exception as exc:  # noqa: BLE001
                log.append(f"Could not double-check the research match, keeping it anyway: {exc}")

    snippets: list[str] = []
    web_results: list[dict] = []
    dim_snippets: dict[str, list[str]] = {}
    if cfg.enable_web:
        queries = [
            f"{lookup} size 2025 2033 CAGR forecast",
            f"{lookup} regional share North America Europe Asia Pacific",
            f"{lookup} market segmentation components pricing",
        ]
        for attempt in range(3):  # the search backend returns nothing when throttled
            try:
                web_results = gather_with_sources(queries)
            except Exception as exc:  # noqa: BLE001
                log.append(f"Could not fetch live web results: {exc}")
            if web_results:
                break
            import time
            time.sleep(8 * (attempt + 1))
        # Queries aimed at a stated market value, kept with their URLs and
        # titles so each cited figure can be traced to a source (cited_values.py).
        # Quoted phrases matter: unquoted, ddgs returned mostly unrelated
        # market reports (Televisions: dining tables, power banks, tickers).
        name = re.sub(r"(?i)^global\s+|\s+market$", "", lookup.strip())
        singular = " ".join(market_tokens(name))
        size_queries = [f'"{name.lower()} market" size 2025 billion']
        if singular and singular != name.lower():
            size_queries.append(f'"{singular} market" size valued 2024 2025')
        size_queries.append(f"global {name} market size 2025 USD billion")
        try:
            size_results = gather_with_sources(size_queries, per_query=8, cap=18)
        except Exception as exc:  # noqa: BLE001
            size_results = []
            log.append(f"Could not fetch market-size web results: {exc}")
        seen = {r["text"][:80].lower() for r in web_results}
        for item in size_results:
            if item["text"][:80].lower() not in seen:
                seen.add(item["text"][:80].lower())
                web_results.append(item)
        snippets = [r["text"] for r in web_results]

        # Per-dimension queries: the broad/flat dimensions (many near-equal
        # segments) are the ones the shares agent gets most wrong when it only
        # has generic market-level evidence to work from.
        for dim, tree in sk.nesting.items():
            root = tree.get("__root__", [])
            label = dim[3:] if dim.startswith("By ") else dim
            dim_queries = [f"{lookup} {label} share breakdown percentage"]
            if len(root) >= 5:
                dim_queries.append(f"{lookup} {', '.join(root[:4])} market share")
            try:
                dim_snippets[dim] = gather(dim_queries, per_query=4, cap=6)
            except Exception as exc:  # noqa: BLE001
                log.append(f"Could not fetch live web results for '{dim}': {exc}")

    db_note = "found matching past research" if db_ev else "no matching past research found"
    log.append(
        f"Evidence gathered: {db_note}, {len(snippets)} live web results, "
        f"{sum(len(v) for v in dim_snippets.values())} category-specific web results"
    )

    size_range = None
    if getattr(cfg, "size_check", False) and cfg.is_online:
        size_range = estimate_size_range(lookup, definition, llm)
        if size_range:
            log.append(
                f"Size check (units x price): {size_range['units_low']:,.0f}-{size_range['units_high']:,.0f} "
                f"{size_range['unit'] or 'units'} x ${size_range['asp_low_usd']:,.0f}-${size_range['asp_high_usd']:,.0f} "
                f"= ${size_range['low_mn']:,.0f}M-${size_range['high_mn']:,.0f}M"
            )
        else:
            log.append("Size check skipped -- no usable units x price estimate")

    cited, sources = _cited_anchor(state, db_ev, web_results, lookup, size_range,
                                   fetch_pages=bool(getattr(cfg, "fetch_pages", False)))
    # Rejected figures must never reach the market-size LLM: mask them in the
    # evidence every later prompt is built from.
    masks = _figure_masks(cited)
    if db_ev is not None and masks:
        db_ev = replace(db_ev, raw_narrative=_mask_text(db_ev.raw_narrative, masks),
                        summary_text=_mask_text(db_ev.summary_text, masks))
    snippets = [_mask_text(s, masks) for s in snippets]
    dim_snippets = {d: [_mask_text(s, masks) for s in v] for d, v in dim_snippets.items()}
    return {
        "db_evidence": db_ev, "web_snippets": snippets, "dim_snippets": dim_snippets, "log": log,
        "web_sources": web_results, "cited_anchor": cited, "evidence_sources": sources,
    }


_MAX_PAGES = 4


def _cited_anchor(state: dict[str, Any], db_ev: DBEvidence | None, web_results: list[dict], lookup: str,
                  size_range: dict[str, Any] | None, *, fetch_pages: bool) -> tuple[dict[str, Any], list[list[str]]]:
    """Consensus 2025 market value from every cited figure in the DB report and
    the web results (snippet + page text per URL, with the page title). Logs
    what was accepted and why figures were rejected for scope or magnitude.
    Also returns the (label, text, title) sources, saved for offline replay."""
    cfg = state.get("config")
    log = state.setdefault("log", [])
    sources: list[list[str]] = []
    if db_ev is not None and db_ev.raw_narrative:
        sources.append([f"CMI report DB: {db_ev.keyword}", db_ev.raw_narrative, db_ev.keyword])
    by_url: dict[str, list[str]] = {}
    titles: dict[str, str] = {}
    for item in web_results:
        key = item.get("url") or item.get("domain") or "web"
        by_url.setdefault(key, []).append(item["text"])
        titles.setdefault(key, item.get("title") or "")
    fetched = 0
    wanted = market_tokens(lookup)
    for url, texts in by_url.items():
        joined = " ".join(texts).lower()
        relevant = "market" in joined and all(t in joined for t in wanted)
        if (fetch_pages and relevant and fetched < _MAX_PAGES and url.startswith("http")
                and not url.lower().endswith(".pdf")):
            page = fetch_page_text(url)
            fetched += 1
            if page:
                texts = texts + [page]
        sources.append([url, "\n".join(texts), titles.get(url, "")])

    llm = state.get("llm")
    anchor = collect_cited_values(
        [tuple(s) for s in sources], lookup, definition=str(state.get("market_definition") or ""),
        llm=llm if getattr(cfg, "is_online", False) else None,
        size_range=size_range,
        title_subject=getattr(cfg, "title_subject", True),
        source_tiers=getattr(cfg, "source_tiers", True),
        weak_consensus=getattr(cfg, "weak_consensus", False),
    )
    for r in anchor.rejected:
        if r.reason.startswith(("scope:", "magnitude:")):
            log.append(f"Rejected cited figure {r.raw_text} ({r.source}) -- {r.reason}")
    if anchor.grounded:
        log.append(f"Cited market size: {anchor.describe()}")
        if anchor.disputed:
            log.append("Cited values are disputed (a single value, or a spread above 2x)")
    else:
        log.append(
            f"No usable cited market-size figure ({len(anchor.rejected)} rejected, "
            f"{len(anchor.corroborating)} tier-2 only)"
        )
    return anchor.to_dict(), sources


_MASK_LABELS = (
    ("scope:", "different scope"), ("magnitude:", "implausible size"), ("outlier:", "outlier"),
    ("forecast", "forecast year"), ("year ", "other year"), ("no year", "no year stated"),
    ("value for a part", "segment or region"), ("incremental", "growth increment"),
)


def _figure_masks(cited: dict[str, Any]) -> dict[str, str]:
    """raw_text -> replacement for every rejected figure that isn't also the
    text of an accepted or corroborating one."""
    keep = {v["raw_text"] for v in (cited.get("accepted") or []) + (cited.get("corroborating") or [])}
    masks: dict[str, str] = {}
    for v in cited.get("rejected") or []:
        raw = v.get("raw_text") or ""
        reason = str(v.get("reason", ""))
        if not raw or raw in keep or raw in masks or reason.startswith(
                ("another figure from the same source", "same figure as")):
            continue
        label = next((lbl for key, lbl in _MASK_LABELS if reason.startswith(key)), "not used")
        masks[raw] = f"[figure removed: {label}]"
    return masks


def _mask_text(text: str, masks: dict[str, str]) -> str:
    if not text or not masks:
        return text
    for raw in sorted(masks, key=len, reverse=True):
        text = re.sub(rf"(?<![\w.$]){re.escape(raw)}(?![\w])", masks[raw], text)
    return text


def market_size_node(state: dict[str, Any]) -> dict[str, Any]:
    cfg = state["config"]
    sk: Skeleton = state["skeleton"]
    llm: GenLLM = state["llm"]
    regions = list(sk.regions.keys())
    system = (
        "You are a market-sizing analyst. Real-data-grounded rule: first check whether the "
        "evidence below contains an EXPLICITLY CITED market-size and/or CAGR figure (a specific "
        "number attributed to a real source — a report, an article, a forecast — not a vague "
        "mention). If it does, your global_value_2025 and global_cagr MUST be derived from and "
        "consistent with that real cited figure (adjusting for year/scope differences with "
        "reasoning is fine; inventing a disconnected number is not). Set \"grounded\": true and "
        "name the cited figure in your reasoning. If NO explicitly cited figure exists anywhere "
        "in the evidence, you must still produce a best estimate using your own market knowledge "
        "(a market cannot be sized as zero) — but set \"grounded\": false so this is tracked "
        "transparently as an ungrounded estimate, not silently presented as evidence-backed. "
        "For each region, give its share of global value (decimals summing to 1) and its own "
        "value CAGR (decimal), grounded the same way wherever the evidence covers a region "
        "specifically."
    )
    refine = ""
    critique = state.get("market_critique", "")
    prior_anchor = state.get("global_anchor_2025")
    if critique and prior_anchor is not None:
        refine = (
            f"\nPREVIOUS ATTEMPT: global_value_2025={prior_anchor}, "
            f"global_cagr={state.get('global_cagr')}, regions={json.dumps(state.get('regional', {}), ensure_ascii=False)}\n"
            f"REVIEWER CRITIQUE: {critique}\n"
            "Apply the critique concretely — adjust the named number(s) in the stated "
            "direction; do not just return the same numbers."
        )
    evidence_text, evidence_items = _evidence_numbered(state)
    cited_anchor = state.get("cited_anchor") or {}
    consensus = cited_anchor.get("value_2025_mn")
    user = (
        f"Market: {sk.market_name}\nRegions: {regions}\n\n{evidence_text}"
        f"{_cited_block(cited_anchor)}{refine}\n\n"
        "global_value_2025 is in US$ MILLIONS (e.g. USD 15.2 billion -> 15200; USD 850 million -> 850).\n"
        'Return JSON: {"global_value_2025": number, "global_cagr": number, "grounded": <true|false>, '
        '"cited_figure": "<the real figure you grounded on, or empty string if none>", '
        '"regions": {"<region>": {"value_share": number, "cagr": number}}, '
        '"reasons": {"global": "<how you reached the global value and CAGR: evidence ids and steps>", '
        '"<region>": "<2-3 sentences: evidence ids used for this region\'s share and CAGR, and where you '
        'used judgement because no evidence covered it>"}}'
    )
    data = llm.complete_json(system, user)
    # Without a cited consensus, an answer equal to a figure we rejected (e.g.
    # the scoped urea run returned 32,710 = the fertilizer-only CMI figure) is
    # discarded and re-asked with that figure named.
    for _ in range(_MAX_REASKS):
        if consensus:
            break
        hit = _matches_rejected(data.get("global_value_2025"), cited_anchor)
        if hit is None:
            break
        state.setdefault("log", []).append(
            f"Discarded an answer of ${float(data.get('global_value_2025')):,.0f}M -- it equals the rejected figure "
            f"{hit['raw_text']} ({hit['reason'][:80]}); asking again"
        )
        user += (
            f"\n\nYOUR PREVIOUS ANSWER ({data.get('global_value_2025')}) equals a REJECTED figure "
            f"({hit['raw_text']}: {hit['reason'][:120]}). It does not describe this market's scope. "
            "Estimate the market for the stated scope instead."
        )
        data = llm.complete_json(system, user)
    market_reasons = data.get("reasons") if isinstance(data.get("reasons"), dict) else {}
    regional_raw = data.get("regions", {}) or {}
    shares = {r: float(regional_raw.get(r, {}).get("value_share", 0.0) or 0.0) for r in regions}
    shares = _normalize(shares)
    regional = {}
    for r in regions:
        cagr = float(regional_raw.get(r, {}).get("cagr", data.get("global_cagr", 0.1)) or 0.1)
        regional[r] = {"value_share": shares[r], "cagr": round(min(0.45, max(0.01, cagr)), 4)}
    global_anchor = float(data.get("global_value_2025", 0.0) or 0.0) or 1000.0
    global_cagr = round(min(0.45, max(0.01, float(data.get("global_cagr", 0.1) or 0.1))), 4)
    grounded = bool(data.get("grounded"))
    cited = str(data.get("cited_figure") or "").strip()

    # Code-level check, not just a prompt instruction: LLMs are unreliable at
    # billion/million conversion in their own arithmetic (verified: the same
    # "USD 214 billion" citation produced $214M in one pass, $214,000M in
    # another, for the identical market). If the cited text itself states a
    # parseable figure and the LLM's own number is off by more than 2x from
    # it, trust the parsed citation instead of the LLM's arithmetic.
    correction_note = ""
    if grounded:
        cited_value_mn = _extract_cited_value_mn(cited)
        if cited_value_mn and cited_value_mn > 0 and not (0.5 * cited_value_mn <= global_anchor <= 2.0 * cited_value_mn):
            correction_note = f" [corrected from ${global_anchor:,.0f}M -- unit mismatch vs cited figure]"
            global_anchor = cited_value_mn
    if not correction_note:
        global_anchor, correction_note = _fix_unit_scale(global_anchor, _evidence_values_mn(state))

    # A grounded run must keep the cited growth rate: the reviewer critique
    # loop otherwise drifts it (seen: cited 6.3% ended at 4.8%).
    if grounded:
        cited_cagr = _extract_cited_cagr(cited)
        if cited_cagr is not None and abs(global_cagr - cited_cagr) > 0.005:
            correction_note += f" [CAGR {global_cagr:.1%} reset to cited {cited_cagr:.1%}]"
            global_cagr = round(cited_cagr, 4)

    # Same drift for the value itself (seen: cited "USD 13.61 billion in 2026"
    # ended at $19,000M). The anchor year is 2025 (the ME pipeline reads it as
    # the 2025 value): use a cited 2025 figure, else a cited 2026 figure taken
    # back one year at the CAGR.
    if grounded and "unit mismatch" not in correction_note:
        cited_2025 = _extract_cited_value_for_year(cited, 2025)
        cited_2026 = _extract_cited_value_for_year(cited, 2026)
        target, why = None, ""
        if cited_2025:
            target, why = cited_2025, "cited 2025 figure"
        elif cited_2026:
            target, why = cited_2026 / (1 + global_cagr), "cited 2026 figure ÷ (1 + CAGR)"
        if target and abs(global_anchor - target) / target > 0.05:
            correction_note += f" [value ${global_anchor:,.0f}M reset to {why}]"
            global_anchor = round(target, 1)

    # Grounded means backed by a cited figure (source + number) we can find in
    # the evidence ourselves -- not just the LLM saying so.
    if consensus:
        source = f"cited figures: {_describe_anchor(cited_anchor)}"
        if abs(global_anchor - consensus) / consensus > 0.005:
            correction_note = f" [value ${global_anchor:,.0f}M reset to cited consensus]"
        else:
            correction_note = ""
        global_anchor = round(float(consensus), 1)
        if cited_anchor.get("cagr") and abs(global_cagr - cited_anchor["cagr"]) > 0.005:
            correction_note += f" [CAGR {global_cagr:.1%} reset to cited {cited_anchor['cagr']:.1%}]"
            global_cagr = round(float(cited_anchor["cagr"]), 4)
        grounded = True
        note = f"grounded on {source}" + correction_note
    else:
        verified = grounded and _citation_verified(cited, state)
        if grounded and not verified:
            correction_note += " [LLM-cited figure not found in the evidence or rejected for scope -- treated as ungrounded]"
        source = f"LLM-cited evidence figure: {cited}" if verified else ""
        note = (f"grounded on: {cited}" if verified and cited else "no cited figure found — ungrounded estimate") + correction_note
        grounded = verified

    # Scope reconciliation (scope.py): the anchor must measure the market the
    # taxonomy describes. Two checks, both cached so the reward loop can't undo them:
    #   judge     -- which end-use segments a CITED figure covers; add the rest
    #   bottom-up -- every end-use segment sized on its own; replaces the anchor when
    #                there is no citable total, or when the segments add up to more
    #                than 1.5x the anchor with at least half of that sum cited
    scope_note = ""
    prev = state.get("scope_reconciliation") or {}
    cache = dict(prev.get("cache") or {})
    scope_rec = prev or None
    if getattr(cfg, "is_online", False) and getattr(cfg, "enable_web", False):
        search = lambda qs: gather(qs, per_query=5, cap=10)  # noqa: E731
        noun = (product_term(sk.market_name).split() or ["product"])[-1]

        def extra(segment: str) -> list[str]:  # CMI's own neighbouring-market reports
            try:
                own = product_term(sk.market_name).lower() + " market'"  # never the market's own report
                return [s for s in (cmi_report_snippets(cfg.neon_url, segment, noun) if cfg.neon_url else [])
                        if own not in s.lower()]
            except Exception:  # noqa: BLE001
                return []
        try:
            if "bottom_up" not in cache:
                cache["bottom_up"] = bottom_up_scope(llm, sk.market_name, sk.nesting, global_anchor, search, extra)
            if grounded and cited and cache.get("judge_for") != cited:
                cache["judge"] = reconcile_scope(llm, sk.market_name, sk.nesting, global_anchor, cited, search, extra)
                cache["judge_for"] = cited
        except Exception as exc:  # noqa: BLE001 -- keep the anchor
            state.setdefault("log", []).append(f"Scope check failed: {exc}")
        bu = cache.get("bottom_up")
        jr = cache.get("judge") if grounded and cache.get("judge_for") == cited else None
        # Unbiased by construction: one firm's total is a cross-check, not the answer. The
        # whole market is the sum of per-segment publisher CONSENSUS whenever that build is
        # backed by 2+ publishers for at least half its value; otherwise fall back.
        chosen, decision = None, "anchor kept"
        if bu and bu["grounded_share_of_added"] >= 0.5:
            chosen, decision = bu, (f"sum of per-segment publisher consensus "
                                    f"({bu['grounded_share_of_added']:.0%} of value from 2+ publishers)"
                                    + (f"; single cited total ${global_anchor:,.0f}M kept as a cross-check"
                                       if grounded else ""))
        elif jr and jr.get("changed"):
            chosen, decision = jr, "cited figure covers only part of the taxonomy"
        elif bu and not grounded:
            chosen, decision = bu, "no citable total"
        elif bu and bu["anchor_mn"] > 1.5 * global_anchor:
            chosen, decision = bu, (f"end-use segments add up to {bu['anchor_mn'] / global_anchor:.1f}x the anchor "
                                    "-- the anchor's scope is narrower than the taxonomy")
        if chosen:
            global_anchor = round(chosen["anchor_mn"], 1)
            scope_note = f" [scope: {decision} -> ${global_anchor:,.0f}M]"
        scope_rec = {**(chosen or {"changed": False}), "decision": decision, "cache": cache}

    note += scope_note
    state.setdefault("log", []).append(
        f"Estimated total market size: ${global_anchor:,.0f}M for 2025, growing {global_cagr:.1%} per year ({note})"
    )
    return {
        "regional": regional, "global_anchor_2025": global_anchor, "global_cagr": global_cagr,
        "market_size_grounded": grounded, "market_size_source": source, "scope_reconciliation": scope_rec,
        "market_trace": {"evidence": evidence_items, "reasons": {k: str(v)[:800] for k, v in market_reasons.items()},
                         "cited_figure": cited, "grounded": grounded, "corrections": correction_note.strip(),
                         "llm_global_value_2025": data.get("global_value_2025"), "llm_global_cagr": data.get("global_cagr"),
                         "llm_regions": data.get("regions")},
    }


def explain_missing_groups(llm: GenLLM, market: str, dim: str, evidence_text: str,
                           shares: dict[str, dict[str, float]], missing: list[str]) -> dict[str, str]:
    """Follow-up: every share group must carry its reasoning. For groups the
    model skipped, ask it to explain the shares it chose (same evidence),
    citing evidence ids or saying plainly where it used judgement."""
    if not missing:
        return {}
    try:
        data = llm.complete_json(
            "You set market-segment shares and must document how. For each group below, explain how its shares "
            "were reached: which evidence ids you relied on and how they became these numbers; say plainly which "
            "segments are judgement because no evidence covered them. Return json.",
            f"Market: {market}\nDimension: {dim}\nShares you set: "
            f"{json.dumps({g: shares.get(g, {}) for g in missing}, ensure_ascii=False)}\n\n{evidence_text}\n\n"
            'Return JSON: {"reasons": {"<group>": "<2-4 sentences>"}}',
        )
    except Exception:  # noqa: BLE001
        return {}
    reasons = data.get("reasons") if isinstance(data.get("reasons"), dict) else {}
    return {g: f"(given on follow-up) {str(reasons[g])[:900]}" for g in missing if reasons.get(g)}


_MAX_REASKS = 2


def _matches_rejected(value: Any, cited_anchor: dict[str, Any]) -> dict[str, Any] | None:
    """The rejected figure an LLM answer equals within +/-1% (as stated or
    moved to 2025), if any."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    for r in cited_anchor.get("rejected") or []:
        reason = str(r.get("reason", ""))
        if reason.startswith(("another figure from the same source", "same figure as")):
            continue  # bookkeeping duplicates, not figures judged wrong
        for ref in (r.get("value_mn"), r.get("value_2025_mn")):
            if ref and abs(v - float(ref)) <= 0.01 * float(ref):
                return r
    return None


def _describe_anchor(cited_anchor: dict[str, Any]) -> str:
    parts = "; ".join(
        f"{v['source']}: {v['raw_text']} ({v['year']})" for v in cited_anchor.get("accepted", [])
    )
    return f"{cited_anchor.get('method')} = ${float(cited_anchor['value_2025_mn']):,.0f}M [{parts}]"


def _cited_block(cited_anchor: dict[str, Any]) -> str:
    """The pre-extracted cited figures the LLM must anchor to, as a prompt
    block. Rejected figures are never shown (they are also masked out of the
    evidence brief): naming them, even as "do not use", put them in reach."""
    accepted = cited_anchor.get("accepted") or []
    if not accepted:
        return ""
    lines = ["\n\nCITED MARKET-SIZE FIGURES (extracted from the evidence, converted to 2025 US$ Mn):"]
    for v in accepted:
        lines.append(f"- {v['source']}: {v['raw_text']} for {v['year']} -> {v['value_2025_mn']:,.0f} (2025)")
    if cited_anchor.get("value_2025_mn"):
        lines.append(
            f"CONSENSUS ANCHOR ({cited_anchor.get('method')}): {float(cited_anchor['value_2025_mn']):,.0f}. "
            "global_value_2025 MUST equal this anchor; set grounded=true and cite these figures."
        )
    return "\n".join(lines)


def _citation_verified(cited: str, state: dict[str, Any]) -> bool:
    """True when the LLM's cited figure is a number that actually appears in
    the evidence and was not rejected for scope."""
    value = _extract_cited_value_mn(cited)
    if not value:
        return False
    rejected = [
        float(v["value_mn"]) for v in (state.get("cited_anchor") or {}).get("rejected") or []
        if str(v.get("reason", "")).startswith("scope:")
    ]
    if any(abs(value - r) <= 0.01 * r for r in rejected):
        return False
    return any(abs(value - e) <= 0.01 * e for e in _evidence_values_mn(state))


def _shares_global(state, sk, llm, regions, critique, prior):
    """One global vector per sibling group (default). Best when shares are
    near-uniform across geographies; every region is set to the same vector."""
    dim_critique = state.get("dim_critique", {}) or {}
    all_shares: dict[str, dict[str, dict[str, dict[str, float]]]] = {}
    trace: dict[str, Any] = {}
    # Desk Research Phase 0 gate (segment_definition.py) -- for any dimension
    # the user has explicitly defined base-year regional shares for, those
    # shares are authoritative and REPLACE this LLM call entirely for that
    # dimension; the LLM is never asked to invent or override them.
    seg_def = state.get("segment_definition")
    user_shares = segment_definition.to_root_shares(seg_def) if seg_def else {}
    for dim in sk.nesting:
        if dim in user_shares:
            all_shares[dim] = user_shares[dim]
            continue
        groups = sk.sibling_groups(dim)
        group_spec = {parent: children for parent, children in groups}
        system = (
            "You are a market segmentation analyst. For each sibling group, assign each "
            "segment a share of its immediate parent (decimals that SUM TO 1.0 within the "
            "group). Base the split on evidence and domain reasoning. Parent '__root__' "
            "means shares of the whole dimension."
        )
        refine = ""
        dcrit = dim_critique.get(dim) or critique
        if dcrit and prior.get(dim, {}).get("__global__"):
            refine = (
                f"\nPREVIOUS ATTEMPT: {json.dumps(prior[dim]['__global__'])}\n"
                f"REVIEWER CRITIQUE for this dimension: {dcrit}\n"
                "Apply the critique concretely — shift the named segments in the stated "
                "direction; do not return the same numbers."
            )
        evidence_text, evidence_items = _evidence_numbered(state, dim)
        user = (
            f"Market: {sk.market_name}\nDimension: {dim}\n"
            f"Sibling groups (parent -> segments): {json.dumps(group_spec, ensure_ascii=False)}\n\n"
            f"{evidence_text}{refine}\n\n"
            'Return JSON: {"shares": {"<parent>": {"<segment>": share, ...}, ...}, '
            '"reasons": {"<parent>": "<2-4 sentences: which evidence ids you used for this group and exactly how '
            'they became these shares (e.g. a cited percentage, a ratio between cited figures); say plainly which '
            'segments are your judgement because no evidence covered them>"}} for every group.'
        )
        resp = llm.complete_json(system, user)
        data = resp.get("shares") if isinstance(resp.get("shares"), dict) else resp
        reasons = resp.get("reasons") if isinstance(resp.get("reasons"), dict) else {}
        glob: dict[str, dict[str, float]] = {}
        for parent, children in groups:
            raw = data.get(parent, {}) or {}
            vec = {seg: float(raw.get(seg, 0.0) or 0.0) for seg in children}
            glob[parent] = _normalize(vec)
        dim_out = {"__global__": glob}
        for region in regions:
            dim_out[region] = {p: dict(v) for p, v in glob.items()}
        all_shares[dim] = dim_out
        group_reasons = {k: str(v)[:900] for k, v in reasons.items()}
        group_reasons.update(explain_missing_groups(llm, sk.market_name, dim, evidence_text, glob,
                                                    [parent for parent, _ in groups if parent not in group_reasons]))
        trace[dim] = {"evidence": evidence_items, "mode": "one worldwide split per group (every region the same)",
                      "group_reasons": group_reasons,
                      "raw_llm_shares": {parent: (data.get(parent) or {}) for parent, _ in groups}}
    log_line = f"Estimated segment splits across {len(all_shares)} categories (e.g. by product type, by application)"
    if user_shares:
        log_line += f"; {len(user_shares)} of them used the user-approved base-year segment definition verbatim"
    state.setdefault("log", []).append(log_line)
    return {"shares": all_shares, "shares_trace": trace}


def _group_global_mean(groups, present_regions):
    """Mean vector per group across regions that supplied it (fallback for gaps)."""
    glob: dict[str, dict[str, float]] = {}
    for parent, children in groups:
        acc = {seg: 0.0 for seg in children}
        cnt = 0
        for reg_map in present_regions.values():
            if parent in reg_map:
                for seg in children:
                    acc[seg] += reg_map[parent].get(seg, 0.0)
                cnt += 1
        glob[parent] = _normalize(acc) if cnt else _normalize({seg: 1.0 for seg in children})
    return glob


def geo_node(state: dict[str, Any]) -> dict[str, Any]:
    """Split each region's value across its member countries (share-of-region + CAGR).

    Produces the per-country anchor/CAGR the ME pipeline's sizing block requires:
    country_value = global_anchor * region_value_share * country_share_of_region.
    """
    sk: Skeleton = state["skeleton"]
    llm: GenLLM = state["llm"]
    regional = state.get("regional", {})
    system = (
        "You are a market geography analyst. For each region, split its value across the "
        "listed member countries: give each country a share of its region (decimals that "
        "SUM TO 1.0 per region) and a plausible value CAGR (decimal). Larger economies "
        "get larger shares; reflect real market maturity."
    )
    user = (
        f"Market: {sk.market_name}\n"
        f"Regions and members: {json.dumps(sk.regions, ensure_ascii=False)}\n"
        f"Region value shares/CAGRs: {json.dumps(regional, ensure_ascii=False)}\n\n"
        f"{_evidence_numbered(state)[0]}\n\n"
        'Return JSON: {"split": {"<region>": {"<country>": {"share_of_region": number, "cagr": number}}}, '
        '"reasons": {"<region>": "<2-3 sentences: evidence ids used, and where you used judgement>"}}'
    )
    reasons: dict[str, Any] = {}
    try:
        resp = llm.complete_json(system, user)
        data = resp.get("split") if isinstance(resp.get("split"), dict) else resp
        reasons = resp.get("reasons") if isinstance(resp.get("reasons"), dict) else {}
    except Exception as exc:  # noqa: BLE001
        data = {}
        state.setdefault("log", []).append(f"Could not split the market by country: {exc}")

    geo: dict[str, dict[str, dict[str, float]]] = {}
    for region, members in sk.regions.items():
        rraw = data.get(region, {}) or {}
        shares = {c: float((rraw.get(c, {}) or {}).get("share_of_region", 0.0) or 0.0) for c in members}
        shares = _normalize(shares)
        region_cagr = regional.get(region, {}).get("cagr", state.get("global_cagr", 0.1))
        geo[region] = {}
        for c in members:
            cagr = float((rraw.get(c, {}) or {}).get("cagr", region_cagr) or region_cagr)
            geo[region][c] = {"share_of_region": shares[c], "cagr": round(min(0.5, max(0.01, cagr)), 4)}
    state.setdefault("log", []).append(
        f"Split the market across {sum(len(v) for v in geo.values())} countries within their regions"
    )
    state["geo_trace"] = {"evidence": _evidence_numbered(state)[1], "reasons": {r: str(v)[:700] for r, v in reasons.items()}}
    return {"geo": geo, "geo_trace": state.get("geo_trace")}


def shares_node(state: dict[str, Any]) -> dict[str, Any]:
    """Decide per-REGION share vectors for each sibling group (not one global vector).

    Output shape: shares[dim][region][parent] = {segment: share}. A synthetic
    "__global__" region holds the cross-region mean and fills any gaps.
    """
    sk: Skeleton = state["skeleton"]
    llm: GenLLM = state["llm"]
    cfg = state["config"]
    regions = list(sk.regions.keys())
    critique = state.get("critique", "")
    prior = state.get("shares", {})

    if not cfg.per_region_shares:
        return _shares_global(state, sk, llm, regions, critique, prior)

    all_shares: dict[str, dict[str, dict[str, dict[str, float]]]] = {}
    trace: dict[str, Any] = {}
    # Desk Research Phase 0 gate (segment_definition.py) -- same bypass as
    # `_shares_global`: a dimension the user has defined base-year regional
    # shares for skips this LLM call entirely.
    seg_def = state.get("segment_definition")
    user_shares = segment_definition.to_root_shares(seg_def) if seg_def else {}

    for dim in sk.nesting:
        if dim in user_shares:
            all_shares[dim] = user_shares[dim]
            continue
        groups = sk.sibling_groups(dim)
        group_spec = {parent: children for parent, children in groups}
        system = (
            "You are a market segmentation analyst. For EACH region, assign each segment a "
            "share of its immediate parent (decimals that SUM TO 1.0 within each sibling "
            "group). Regions genuinely differ (e.g. RFID/AI adoption is higher in "
            "developed markets, cash usage higher in emerging ones) — reflect that with "
            "distinct per-region splits, not copies. Parent '__root__' = shares of the "
            "whole dimension."
        )
        refine = ""
        dcrit = (state.get("dim_critique", {}) or {}).get(dim) or critique
        if dcrit and prior.get(dim, {}).get("__global__"):
            refine = (
                f"\nPREVIOUS GLOBAL-MEAN ATTEMPT: {json.dumps(prior[dim]['__global__'])}\n"
                f"REVIEWER CRITIQUE: {dcrit}\nProduce an improved, better-differentiated split."
            )
        evidence_text, evidence_items = _evidence_numbered(state, dim)
        user = (
            f"Market: {sk.market_name}\nDimension: {dim}\nRegions: {regions}\n"
            f"Sibling groups (parent -> segments): {json.dumps(group_spec, ensure_ascii=False)}\n\n"
            f"{evidence_text}{refine}\n\n"
            'Return JSON: {"shares": {"<region>": {"<parent>": {"<segment>": share, ...}, ...}, ...}, '
            '"reasons": {"<region>": "<2-3 sentences: which evidence ids you used and how you turned them into '
            'these shares; say plainly where you used judgement because no evidence covered it>"}} '
            "for EVERY region and EVERY group."
        )
        resp = llm.complete_json(system, user)
        data = resp.get("shares") if isinstance(resp.get("shares"), dict) else resp
        reasons = resp.get("reasons") if isinstance(resp.get("reasons"), dict) else {}

        present_regions: dict[str, dict[str, dict[str, float]]] = {}
        for region in regions:
            rraw = data.get(region, {}) or {}
            reg_map: dict[str, dict[str, float]] = {}
            for parent, children in groups:
                praw = rraw.get(parent, {}) or {}
                vec = {seg: float(praw.get(seg, 0.0) or 0.0) for seg in children}
                if sum(vec.values()) > 0:
                    reg_map[parent] = _normalize(vec)
            if reg_map:
                present_regions[region] = reg_map

        glob = _group_global_mean(groups, present_regions)
        dim_out: dict[str, dict[str, dict[str, float]]] = {"__global__": glob}
        for region in regions:
            reg_map = present_regions.get(region, {})
            for parent, _children in groups:
                if parent not in reg_map:
                    reg_map[parent] = dict(glob[parent])
            dim_out[region] = reg_map
        all_shares[dim] = dim_out
        trace[dim] = {"evidence": evidence_items, "reasons": {r: str(v)[:700] for r, v in reasons.items()},
                      "llm_regions": sorted(present_regions), "filled_from_global_mean":
                      [r for r in regions if r not in present_regions]}

    filled = sum(1 for d in all_shares.values() for r in d if r != "__global__")
    log_line = (
        f"Estimated segment splits across {len(all_shares)} categories, customized for "
        f"{len(regions)} regions ({filled} distinct region-level splits)"
    )
    if user_shares:
        log_line += f"; {len(user_shares)} of them used the user-approved base-year segment definition verbatim"
    state.setdefault("log", []).append(log_line)
    return {"shares": all_shares, "shares_trace": trace}


def finalize_node(state: dict[str, Any]) -> dict[str, Any]:
    """After the loop, restore the best-scoring full snapshot seen across iterations.

    Covers shares AND market_size/geo/asp, since all four are now refined inside
    the reward loop (see reward_node) — a later, worse-scoring iteration should
    not overwrite an earlier, better one for any of them.
    """
    log = state.setdefault("log", [])
    history = state.get("pass_history") or []
    cur_reward = float(state.get("reward", -1.0))
    update: dict[str, Any] = {}
    if history:
        choice = select_best_pass(history)
        chosen = history[choice["index"]]
        last = history[-1]
        if choice["index"] != len(history) - 1:
            if chosen["reward"] > last["reward"]:
                log.append(
                    f"Kept the best pass from earlier -- it scored higher ({chosen['reward']:.0%} vs {last['reward']:.0%})"
                )
            elif chosen["grounded"] and not last["grounded"]:
                log.append(
                    f"Kept pass {chosen['iter']} -- its market size is backed by a cited figure "
                    f"(the last pass was not)"
                )
            else:
                log.append(f"Kept pass {chosen['iter']} -- its market size is closest to the median of all passes")
            update = {**chosen["snapshot"], "reward": chosen["reward"]}
        if choice["unstable"]:
            vals = ", ".join(f"${v:,.0f}M" for v in choice["values"])
            final_value = choice["anchor_override"] or chosen["global_anchor_2025"]
            log.append(
                f"Market size was unstable across passes ({vals}) -- used the median ${final_value:,.0f}M"
            )
            update.setdefault("reward", chosen["reward"])
            update["market_size_unstable"] = {"values": choice["values"], "median": final_value}
            if choice["anchor_override"] is not None:
                update["global_anchor_2025"] = choice["anchor_override"]
                update["market_size_source"] = "median of ungrounded estimates across passes"
    else:
        best = state.get("best_state")
        best_reward = float(state.get("best_reward", -1.0))
        if best is not None and best_reward > cur_reward:
            log.append(
                f"Kept the best pass from earlier -- it scored higher ({best_reward:.0%} vs {cur_reward:.0%})"
            )
            update = {**best, "reward": best_reward}

    flags = compute_demand_quality_flags({**state, **update})
    if flags:
        codes = ", ".join(f.get("code", "") for f in flags)
        state.setdefault("log", []).append(f"Quality check flagged this run for review: {codes}")
    update["quality_flags"] = flags
    return update


def asp_node(state: dict[str, Any]) -> dict[str, Any]:
    sk: Skeleton = state["skeleton"]
    llm: GenLLM = state["llm"]
    products = sk.ordered_segments(sk.priced_dimension)
    system = (
        "You are a pricing analyst. Assign an average selling price (ASP) in US$ per unit "
        "for each product below, reflecting realistic relative pricing in this market. "
        "Return a positive number for EVERY product (including roll-up categories)."
    )
    refine = ""
    critique = state.get("asp_critique", "")
    prior_asp = state.get("asp")
    if critique and prior_asp:
        refine = (
            f"\nPREVIOUS ATTEMPT: {json.dumps(prior_asp, ensure_ascii=False)}\n"
            f"REVIEWER CRITIQUE: {critique}\n"
            "Apply the critique concretely — reprice the named product(s) in the stated "
            "direction; do not just return the same numbers."
        )
    user = (
        f"Market: {sk.market_name}\nPriced products ({sk.priced_dimension}): "
        f"{json.dumps(products, ensure_ascii=False)}\n\n{_evidence_numbered(state)[0]}{refine}\n\n"
        'Return JSON: {"prices": {"<product>": asp_number, ...}, "reasons": {"<product>": "<one sentence: '
        'evidence id used, or that it is your judgement>"}}'
    )
    resp = llm.complete_json(system, user)
    data = resp.get("prices") if isinstance(resp.get("prices"), dict) else resp
    asp_reasons = resp.get("reasons") if isinstance(resp.get("reasons"), dict) else {}
    asp = {}
    for leaf in products:
        try:
            val = float(data.get(leaf, 0.0) or 0.0)
        except (TypeError, ValueError):
            val = 0.0
        asp[leaf] = round(val if val > 0 else 100.0, 2)
    state.setdefault("log", []).append(f"Estimated prices for {len(asp)} products")
    return {"asp": asp, "asp_trace": {"evidence": _evidence_numbered(state)[1],
                                      "reasons": {k: str(v)[:400] for k, v in asp_reasons.items()}}}


def brain_node(state: dict[str, Any]) -> dict[str, Any]:
    sk: Skeleton = state["skeleton"]
    llm: GenLLM = state["llm"]
    regional = state.get("regional", {})
    system = (
        "You are a 'brain' analyst that interrogates the numbers. Pose the key WHY "
        "questions about this market's growth, regional split, and pricing, then answer "
        "each with a concise justification citing drivers and CURRENT EVENTS where "
        "relevant (e.g. supply shocks, regulation, conflicts). Keep each answer 1-3 "
        "sentences."
    )
    user = (
        f"Market: {sk.market_name}\n"
        f"Global 2025 value: {state.get('global_anchor_2025')} US$ Mn, CAGR {state.get('global_cagr')}\n"
        f"Regional split: {json.dumps(regional, ensure_ascii=False)}\n\n{_evidence_brief(state)}\n\n"
        'Return JSON: {"artifacts": [{"topic": str, "question": str, "answer": str, '
        '"drivers": [str], "current_events": [str]}]}'
    )
    try:
        data = llm.complete_json(system, user)
        artifacts = data.get("artifacts", []) or []
    except Exception as exc:  # noqa: BLE001
        artifacts = []
        state.setdefault("log", []).append(f"Could not generate the reasoning notes: {exc}")
    state.setdefault("log", []).append(
        f"Wrote {len(artifacts)} reasoning notes explaining the 'why' behind these numbers"
    )
    return {"artifacts": artifacts}


def _regional_spread(shares: dict, sk: Skeleton, regions: list[str], top_n: int = 5) -> dict[str, list[str]]:
    """Per-dimension summary of how much a segment's share actually varies across
    regions (min-max). Lets the reviewer judge whether per-region differentiation
    looks realistic (not just check the global-mean shape, which never sees this)."""
    out: dict[str, list[str]] = {}
    for dim in sk.nesting:
        dim_map = shares.get(dim, {})
        rows: list[tuple[float, str]] = []
        for parent, children in sk.sibling_groups(dim):
            for seg in children:
                vals = [dim_map.get(r, {}).get(parent, {}).get(seg) for r in regions]
                vals = [v for v in vals if isinstance(v, (int, float))]
                if len(vals) >= 2:
                    spread = max(vals) - min(vals)
                    if spread > 0.03:
                        rows.append((spread, f"{parent}/{seg}: {min(vals):.2f}-{max(vals):.2f}"))
        rows.sort(key=lambda r: r[0], reverse=True)
        if rows:
            out[dim] = [r[1] for r in rows[:top_n]]
    return out


def reward_node(state: dict[str, Any]) -> dict[str, Any]:
    """Deterministic consistency + LLM plausibility -> reward in [0,1] + critique.

    Scores (and produces targeted critiques for) shares, market size/regional
    split, AND asp — all four are refined by the loop, not just shares.
    """
    cfg = state["config"]
    sk: Skeleton = state["skeleton"]
    llm: GenLLM = state["llm"]
    shares = state.get("shares", {})
    regional = state.get("regional", {})

    # 1) deterministic consistency (across every region vector)
    regions = list(sk.regions.keys())
    checks: list[float] = []
    problems: list[str] = []
    for dim in sk.nesting:
        for parent, children in sk.sibling_groups(dim):
            for region in regions:
                vec = shares.get(dim, {}).get(region, {}).get(parent, {})
                s = sum(vec.values())
                ok = abs(s - 1.0) < 0.02 and len(vec) == len(children)
                checks.append(1.0 if ok else 0.0)
                if not ok:
                    problems.append(f"{dim}/{parent}@{region} sums to {s:.3f}")
                if len(children) > 2 and max(vec.values(), default=0) > 0.97:
                    problems.append(f"{dim}/{parent}@{region} degenerate (one segment dominates)")
    rshare = sum(r["value_share"] for r in regional.values()) if regional else 0.0
    region_ok = 1.0 if abs(rshare - 1.0) < 0.02 else 0.0
    if region_ok < 1.0:
        problems.append(f"region shares sum to {rshare:.3f}")
    consistency = (sum(checks) / len(checks) if checks else 0.0) * 0.7 + region_ok * 0.3

    # 2) LLM plausibility vote — scored PER DIMENSION, plus market size and asp,
    #    so critique can be targeted at whichever family of numbers is weakest.
    plaus = 0.6
    dim_critique: dict[str, str] = {}
    market_crit = ""
    asp_crit = ""
    reg_crit = ""
    global_shares = {dim: shares.get(dim, {}).get("__global__", {}) for dim in sk.nesting}
    spread = _regional_spread(shares, sk, regions) if cfg.per_region_shares else {}
    it = int(state.get("iter", 0)) + 1
    try:
        system = (
            "You are a decisive, skeptical market reviewer. For EACH dimension, judge how "
            "plausible its segmentation split is for THIS specific market and rate it 0-1 "
            "(use the full range — be critical; a generic or evenly-spread split should "
            "score low). Give a concrete, actionable critique naming which segment is over- "
            "or under-weighted and the direction to change it. Also rate: the regional value "
            "split and growth; the global market size + CAGR; and the ASP (pricing) set. If a "
            "per-region variation summary is given, judge whether that differentiation looks "
            "realistic for this market (too flat/uniform for a market where regions should "
            "genuinely differ, or too extreme, should score low). Vary your scores; do not "
            "give everything the same number."
        )
        spread_block = (
            f"Regional variation (min-max share across regions, notable spreads only): "
            f"{json.dumps(spread, ensure_ascii=False)[:2000]}\n" if spread else ""
        )
        user = (
            f"Market: {sk.market_name}\nIteration: {it}\n"
            f"Global value 2025: {state.get('global_anchor_2025')} US$ Mn, "
            f"CAGR: {state.get('global_cagr')}\n"
            f"Regional: {json.dumps(regional, ensure_ascii=False)}\n"
            f"ASP: {json.dumps(state.get('asp', {}), ensure_ascii=False)[:2000]}\n"
            f"Global-mean shares per dimension: {json.dumps(global_shares, ensure_ascii=False)[:5000]}\n"
            f"{spread_block}\n"
            'Return JSON: {"overall": number, "dimensions": {"<dim>": {"score": number, '
            '"critique": string}}, "regional_critique": string, '
            '"market_size": {"score": number, "critique": string}, '
            '"asp": {"score": number, "critique": string}}'
        )
        data = llm.complete_json(system, user)
        dims = data.get("dimensions", {}) or {}
        scores = []
        dim_score: dict[str, float] = {}
        for dim in sk.nesting:
            entry = dims.get(dim, {}) or {}
            sc = entry.get("score")
            if isinstance(sc, (int, float)):
                clipped = min(1.0, max(0.0, float(sc)))
                scores.append(clipped)
                dim_score[dim] = clipped
            crit = str(entry.get("critique", "")).strip()
            if crit:
                dim_critique[dim] = crit

        market_entry = data.get("market_size", {}) or {}
        m_sc = market_entry.get("score")
        market_score = None
        if isinstance(m_sc, (int, float)):
            market_score = min(1.0, max(0.0, float(m_sc)))
            scores.append(market_score)
        market_crit = str(market_entry.get("critique", "")).strip()

        asp_entry = data.get("asp", {}) or {}
        a_sc = asp_entry.get("score")
        asp_score = None
        if isinstance(a_sc, (int, float)):
            asp_score = min(1.0, max(0.0, float(a_sc)))
            scores.append(asp_score)
        asp_crit = str(asp_entry.get("critique", "")).strip()

        reg_crit = str(data.get("regional_critique", ""))
        plaus = (sum(scores) / len(scores)) if scores else min(1.0, max(0.0, float(data.get("overall", 0.6) or 0.6)))
    except Exception as exc:  # noqa: BLE001
        state.setdefault("log", []).append(f"Reviewer could not evaluate this pass: {exc}")
        dim_score, market_score, asp_score = {}, None, None

    # An ungrounded market size is penalised, so a self-consistent pass built on
    # a guessed total cannot outscore one built on a cited figure.
    grounded = state.get("market_size_grounded") is True
    penalty = 0.0 if grounded else UNGROUNDED_PENALTY
    reward = round(max(0.0, 0.4 * consistency + 0.6 * plaus - penalty), 4)
    if not grounded:
        market_crit = " ".join(x for x in [
            market_crit, "The global market size is not backed by any cited figure; anchor it to a cited value if the evidence has one.",
        ] if x)
    # global critique string (consistency problems + regional note) as a fallback
    fallback = "; ".join(problems[:4])
    critique = " | ".join(x for x in [fallback, reg_crit] if x)

    # Every pass is kept (snapshot of EVERYTHING the loop refines, not just
    # shares) so finalize can pick by groundedness first, then reward.
    snapshot = {
        "shares": shares,
        "regional": regional,
        "global_anchor_2025": state.get("global_anchor_2025"),
        "global_cagr": state.get("global_cagr"),
        "market_size_grounded": grounded,
        "market_size_source": state.get("market_size_source", ""),
        "geo": state.get("geo"),
        "asp": state.get("asp"),
        "shares_trace": state.get("shares_trace"),
        "market_trace": state.get("market_trace"),
        "geo_trace": state.get("geo_trace"),
        "asp_trace": state.get("asp_trace"),
        "dim_score": dim_score,
        "market_score": market_score,
        "asp_score": asp_score,
    }
    history = list(state.get("pass_history") or []) + [{
        "iter": it, "reward": reward, "grounded": grounded,
        "global_anchor_2025": state.get("global_anchor_2025"), "snapshot": snapshot,
    }]
    choice = select_best_pass(history)
    best = history[choice["index"]]
    update = {
        "reward": reward, "critique": critique, "dim_critique": dim_critique,
        "market_critique": market_crit, "asp_critique": asp_crit, "iter": it,
        "dim_score": dim_score, "market_score": market_score, "asp_score": asp_score,
        "pass_history": history, "best_reward": best["reward"], "best_state": best["snapshot"],
    }
    state.setdefault("log", []).append(
        f"Review pass {it}: confidence {reward:.0%} (consistency {consistency:.0%}, "
        f"plausibility {plaus:.0%}{'' if grounded else f', -{penalty:.0%} ungrounded market size'}) "
        f"-- best so far: {best['reward']:.0%}"
    )
    return update


UNGROUNDED_PENALTY = 0.15
UNSTABLE_RATIO = 2.0


def select_best_pass(history: list[dict[str, Any]]) -> dict[str, Any]:
    """Pick the pass finalize should keep.

    A pass whose market size is grounded in a cited figure always beats an
    ungrounded one; reward only breaks ties within each group. When the
    ungrounded values differ by more than 2x across passes (Televisions gave
    7.8 Bn, 258 Bn, 245 Bn, 245 Bn), the outlier is never chosen: the median
    of the ungrounded values becomes the market size, carried by the pass
    closest to it.

    Returns {"index", "anchor_override" (float | None), "unstable" (bool),
    "values" (ungrounded values seen)}.
    """
    grounded = [i for i, p in enumerate(history) if p.get("grounded")]
    if grounded:
        idx = max(grounded, key=lambda i: (history[i]["reward"], -i))
        return {"index": idx, "anchor_override": None, "unstable": False, "values": []}
    values = [float(p.get("global_anchor_2025") or 0.0) for p in history]
    positive = [v for v in values if v > 0]
    if len(positive) >= 2 and max(positive) / min(positive) > UNSTABLE_RATIO:
        med = median(positive)
        idx = min(
            (i for i, v in enumerate(values) if v > 0),
            key=lambda i: (abs(math.log(values[i] / med)), -history[i]["reward"], i),
        )
        override = None if abs(values[idx] - med) <= 1e-9 * med else round(med, 1)
        return {"index": idx, "anchor_override": override, "unstable": True, "values": values}
    idx = max(range(len(history)), key=lambda i: (history[i]["reward"], -i))
    return {"index": idx, "anchor_override": None, "unstable": False, "values": values}
