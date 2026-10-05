"""Segmentation dimensions + per-country segment shares, and the approval gate.

Flow (one human gate, same discipline as the other demand-side phases):

    build_segments_draft  -> propose dimensions (CMI report DB taxonomy if it
                             can be matched, otherwise LLM + web), then one
                             web-grounded LLM share estimate per dimension
    (human edits the draft JSON)
    approve_segments      -> validates + renormalizes shares (hard-fails on
                             missing countries / bad edits)
    finalize_report       -> deterministic split + rollup (split.py)

Every share set carries an `evidence_status`: "web_evidence_backed" only when
at least one LLM-quoted figure is found verbatim in the fetched snippets;
otherwise "assumption". The status is reported, never hidden.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable

from me_engine.generation.llm import GenLLM

from . import split

SearchFn = Callable[[list[str]], list[str]]

# Dimension names that mean "application / end use". In approach 2B that
# dimension is already built bottom-up, so it is never split top-down again.
_APPLICATION_WORDS = ("application", "end use", "end-use", "end user", "end-user", "use case", "usage")
_GEOGRAPHY_WORDS = ("region", "geograph", "country", "countries")

_PROPOSE_SYSTEM = (
    "You are a senior market research analyst defining the segmentation taxonomy of a "
    "syndicated market study. Respond ONLY with JSON."
)
_SHARES_SYSTEM = (
    "You are a senior market research analyst estimating how a market's value splits across "
    "the segments of one segmentation dimension, country by country. Base your numbers on the "
    "web snippets where they give figures; otherwise use your best industry knowledge and say so. "
    "Respond ONLY with JSON."
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_search(queries: list[str]) -> list[str]:
    from me_engine.generation.web_evidence import gather

    return gather(queries, per_query=4, cap=12)


def is_application_dimension(name: str) -> bool:
    low = name.lower()
    return any(w in low for w in _APPLICATION_WORDS)


def _is_geography_dimension(name: str) -> bool:
    low = name.lower()
    return any(w in low for w in _GEOGRAPHY_WORDS)


def _clean_segments(raw: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for s in raw or []:
        name = str(s).strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out


# --------------------------------------------------------------------------- #
# 1. Dimensions
# --------------------------------------------------------------------------- #
def dimensions_from_report_db(market_name: str, config: Any) -> list[dict[str, Any]]:
    """Labels-only taxonomy from the CMI report DB (no numbers). [] if unavailable."""
    if not getattr(config, "neon_url", None):
        return []
    try:
        from me_engine.generation.dynamic_skeleton import load_skeleton_from_db

        skeleton, _ev = load_skeleton_from_db(market_name, config)
    except Exception:  # noqa: BLE001 -- the DB is optional; fall back to the LLM
        return []
    if skeleton is None:
        return []
    dims: list[dict[str, Any]] = []
    for dim in skeleton.nesting:
        if _is_geography_dimension(dim):
            continue
        segments = _clean_segments(skeleton.leaves(dim)) or _clean_segments(skeleton.nesting[dim].get("__root__"))
        if len(segments) >= 2:
            dims.append({"dimension": dim, "segments": segments, "taxonomy_source": "cmi_report_db"})
    return dims


def propose_dimensions_llm(
    llm: GenLLM, market_name: str, scope: str, exclude_application: bool,
) -> list[dict[str, Any]]:
    avoid = (
        " Do NOT include an Application / End-Use dimension (it is estimated separately, bottom-up)."
        if exclude_application else
        " Include the Application / End-Use dimension if the market has one."
    )
    user = (
        f"Market: {market_name}\nScope: {scope}\n\n"
        "Propose the 2-4 segmentation dimensions a market study of this market would report "
        "(e.g. By Product Type, By Application, By Distribution Channel, By Form, By Source). "
        "Segments inside a dimension must be mutually exclusive and together cover the whole "
        "market (3-8 segments each). Never include a geography dimension." + avoid + "\n\n"
        'Respond as {"dimensions": [{"dimension": "By ...", "segments": ["..."], "rationale": "..."}]}'
    )
    data = llm.complete_json(_PROPOSE_SYSTEM, user)
    dims: list[dict[str, Any]] = []
    for d in data.get("dimensions") or []:
        name = str(d.get("dimension") or "").strip()
        segments = _clean_segments(d.get("segments"))
        if not name or len(segments) < 2 or _is_geography_dimension(name):
            continue
        dims.append({
            "dimension": name, "segments": segments, "taxonomy_source": "llm_proposed",
            "rationale": str(d.get("rationale") or ""),
        })
    return dims


def report_taxonomy_fits(llm: GenLLM, market_name: str, scope: str, dims: list[dict[str, Any]]) -> bool:
    """The report DB can match a neighbouring report (e.g. "Crude Oil" -> a
    crude-oil desalter equipment study). Accept its taxonomy only if the LLM
    confirms the segments describe THIS market's own product/demand."""
    listing = "\n".join(f"- {d['dimension']}: {d['segments']}" for d in dims)
    user = (
        f"Market: {market_name}\nScope: {scope}\n\nCandidate segmentation taken from a stored report:\n"
        f"{listing}\n\nDo these dimensions segment THIS market itself (its own product, forms, "
        "applications, buyers, channels), rather than a different market such as equipment, "
        "services or a downstream/upstream product? Respond as "
        '{"fits": true|false, "reason": "..."}'
    )
    data = llm.complete_json(_PROPOSE_SYSTEM, user)
    return data.get("fits") is True


def propose_dimensions(
    llm: GenLLM, config: Any, market_name: str, scope: str, exclude_application: bool,
) -> list[dict[str, Any]]:
    dims = dimensions_from_report_db(market_name, config)
    if dims and not report_taxonomy_fits(llm, market_name, scope, dims):
        dims = []
    if not dims:
        dims = propose_dimensions_llm(llm, market_name, scope, exclude_application)
    if exclude_application:
        dims = [d for d in dims if not is_application_dimension(d["dimension"])]
    return dims


# --------------------------------------------------------------------------- #
# 2. Shares
# --------------------------------------------------------------------------- #
def _norm_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def _quote_verified(quote: str, snippets: list[str]) -> bool:
    q = _norm_text(quote)
    if len(q) < 8 or not re.search(r"\d", q):
        return False
    return any(q in _norm_text(s) for s in snippets)


def estimate_shares(
    llm: GenLLM,
    market_name: str,
    dimension: dict[str, Any],
    countries: list[str],
    base_year: int | None,
    search: SearchFn | None = None,
) -> dict[str, Any]:
    """One web-grounded LLM call per dimension -> per-country shares + evidence."""
    dim_name = dimension["dimension"]
    segments = dimension["segments"]
    topic = re.sub(r"^\s*by\s+", "", dim_name, flags=re.I)
    queries = [
        f"{market_name} market share by {topic}",
        f"{market_name} market {topic} segment share {base_year or ''}".strip(),
        f"{market_name} {segments[0]} segment share percent",
    ]
    snippets = (search or default_search)(queries)
    snippet_block = "\n".join(f"- {s}" for s in snippets) or "(no web snippets found)"
    user = (
        f"Market: {market_name}\nBase year: {base_year}\nDimension: {dim_name}\n"
        f"Segments: {segments}\nCountries: {countries}\n\n"
        f"Web snippets:\n{snippet_block}\n\n"
        "For EVERY country, give each segment's share of that country's market value (fractions "
        "summing to 1). Countries may differ where you have reason to believe so. In "
        "`evidence`, copy any snippet sentence fragment that contains a figure you relied on, "
        "VERBATIM. Respond as "
        '{"shares": {"<country>": {"<segment>": <0-1>}}, '
        '"evidence": [{"segment": "...", "quote": "..."}], "reasoning": "..."}'
    )
    data = llm.complete_json(_SHARES_SYSTEM, user)

    raw_shares = data.get("shares") or {}
    shares: dict[str, dict[str, float]] = {}
    flags: list[str] = []
    for c in countries:
        normalized, equal = split.normalize_shares(raw_shares.get(c) or {}, segments)
        shares[c] = normalized
        if equal:
            flags.append(f"{c}: no shares returned -- equal split used (assumption)")

    evidence = []
    for e in data.get("evidence") or []:
        quote = str(e.get("quote") or "").strip()
        if quote:
            evidence.append({
                "segment": str(e.get("segment") or ""), "quote": quote,
                "verified_in_snippets": _quote_verified(quote, snippets),
            })
    backed = any(e["verified_in_snippets"] for e in evidence)
    return {
        **dimension,
        "shares": shares,
        "evidence_status": "web_evidence_backed" if backed else "assumption",
        "evidence": evidence,
        "snippet_count": len(snippets),
        "reasoning": str(data.get("reasoning") or ""),
        "flags": flags,
    }


# --------------------------------------------------------------------------- #
# 3. Draft / approve / finalize
# --------------------------------------------------------------------------- #
def build_segments_draft(
    *,
    llm: GenLLM,
    config: Any,
    approach: str,
    market_name: str,
    scope: str,
    base_year: int | None,
    country_totals: dict[str, float],
    bottom_up_dimensions: list[dict[str, Any]] | None = None,
    search: SearchFn | None = None,
) -> dict[str, Any]:
    countries = list(country_totals)
    bottom_up_dimensions = bottom_up_dimensions or []
    dims = propose_dimensions(llm, config, market_name, scope, exclude_application=bool(bottom_up_dimensions))
    top_down = [estimate_shares(llm, market_name, d, countries, base_year, search) for d in dims]
    return {
        "market_name": market_name,
        "approach": approach,
        "base_year": base_year,
        "countries": countries,
        "country_totals_usd_mn": country_totals,
        "bottom_up_dimensions": bottom_up_dimensions,
        "top_down_dimensions": top_down,
        "created_at": _now_iso(),
        "note": (
            "Edit `top_down_dimensions` freely (rename/add/remove segments, change shares). "
            "country_totals_usd_mn and bottom_up_dimensions come from the sizing run and are read-only."
        ),
    }


def approve_segments(edited: dict[str, Any], approved_by: str | None = None) -> dict[str, Any]:
    market_name = str(edited.get("market_name") or "").strip()
    if not market_name:
        raise ValueError("segments draft is missing market_name")
    totals = edited.get("country_totals_usd_mn") or {}
    if not totals:
        raise ValueError("segments draft has no country_totals_usd_mn -- run sizing first")
    countries = list(totals)

    approved_dims: list[dict[str, Any]] = []
    for d in edited.get("top_down_dimensions") or []:
        name = str(d.get("dimension") or "").strip()
        segments = _clean_segments(d.get("segments"))
        if not name:
            raise ValueError("a top-down dimension is missing its name")
        if len(segments) < 2:
            raise ValueError(f"dimension '{name}' needs at least 2 segments")
        raw = d.get("shares") or {}
        missing = [c for c in countries if c not in raw]
        if missing:
            raise ValueError(f"dimension '{name}' has no shares for: {missing}")
        shares: dict[str, dict[str, float]] = {}
        for c in countries:
            row = raw[c] or {}
            unknown = [s for s in row if s not in segments]
            if unknown:
                raise ValueError(f"dimension '{name}', {c}: shares given for unknown segment(s) {unknown}")
            total = sum(float(v or 0.0) for v in row.values())
            if total > 0 and abs(total - 1.0) > 0.02 and abs(total - 100.0) > 2.0:
                raise ValueError(
                    f"dimension '{name}', {c}: shares sum to {total:.3f}; they must sum to 1 (or 100%)"
                )
            shares[c], _ = split.normalize_shares(row, segments)
        approved_dims.append({**d, "dimension": name, "segments": segments, "shares": shares})

    if not approved_dims and not edited.get("bottom_up_dimensions"):
        raise ValueError("nothing to segment: no top-down or bottom-up dimensions")

    return {
        **edited,
        "top_down_dimensions": approved_dims,
        "approved": True,
        "approved_by": approved_by,
        "approved_at": _now_iso(),
    }


def finalize_report(approved: dict[str, Any], methodology_note: str) -> dict[str, Any]:
    totals = {c: float(v or 0.0) for c, v in (approved.get("country_totals_usd_mn") or {}).items()}
    tables: list[dict[str, Any]] = []
    for d in approved.get("bottom_up_dimensions") or []:
        tables.append(split.build_dimension_table(
            d["dimension"], "bottom_up", d["segments"], d["country_values"],
            extra={"evidence_status": "see 02_sizing build-up rows"},
        ))
    for d in approved.get("top_down_dimensions") or []:
        values = split.split_top_down(totals, d["shares"], d["segments"])
        tables.append(split.build_dimension_table(
            d["dimension"], "top_down", d["segments"], values,
            extra={
                "shares": d["shares"],
                "taxonomy_source": d.get("taxonomy_source", ""),
                "evidence_status": d.get("evidence_status", "assumption"),
                "evidence": d.get("evidence", []),
                "reasoning": d.get("reasoning", ""),
                "flags": d.get("flags", []),
            },
        ))
    return {
        "market_name": approved["market_name"],
        "approach": approved.get("approach"),
        "methodology": methodology_note,
        "base_year": approved.get("base_year"),
        "unit": "USD Mn",
        "geography": split.geography_summary(totals),
        "segmentation": tables,
        "consistency_checks": split.consistency_checks(totals, tables),
        "generated_at": _now_iso(),
    }
