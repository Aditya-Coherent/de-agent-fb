"""Rich export profiles — parent, role detail, products, geo, rationale for CSV."""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any

from vendor_intel.config import Settings
from vendor_intel.pipeline.csv_fields import extract_company_summary

_PROFILE_SYSTEM = """You write company profiles for a B2B market landscape CSV.
Use ONLY the provided website excerpt, classification, and query context.
Do not invent parent companies, products, or countries not supported by the text.
If a field is unknown from the evidence, use "Not stated on website".
Return JSON only with this exact shape:
{
  "parent_or_independent": "e.g. Independent (UK) OR Brand of X, owned by Y (Country)",
  "role_detail": "1-2 sentences: this company's core role in the target market value chain",
  "product_system_types": "semicolon-separated list of relevant products/systems for the target market",
  "geographic_presence": "operational presence: HQ, plants, and markets served (especially target geography)",
  "company_description": "2-4 sentences plain English: what the company does",
  "inclusion_rationale": "2-3 sentences: why this company belongs in this market landscape for the target geography"
}"""

_NAV_JUNK = re.compile(
    r"\[(?:read more|discover more|learn more|home|menu|skip to)\]",
    re.I,
)

_PROFILE_TIMEOUT_S = float(os.getenv("PIPELINE_PROFILE_TIMEOUT", "75"))
_PROFILE_WORKERS = max(1, min(6, int(os.getenv("PIPELINE_PROFILE_WORKERS", "3"))))


def _crawl_text(smart_data: dict[str, Any], verdict: dict[str, Any], *, max_len: int = 4500) -> str:
    parts: list[str] = []
    summary = str(verdict.get("company_summary") or "").strip()
    if summary:
        parts.append(summary)
    pages = smart_data.get("pages") or []
    if pages and isinstance(pages[0], dict):
        pt = str(pages[0].get("text") or "").strip()
        if pt and pt not in summary:
            parts.append(pt[:3000])
    data = smart_data.get("data") or {}
    if isinstance(data, dict):
        intel = data.get("intel") or {}
        if isinstance(intel, dict):
            for key in ("summary", "synthesis"):
                val = intel.get(key)
                if val:
                    parts.append(str(val))
    text = _NAV_JUNK.sub(" ", " ".join(parts))
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text[:max_len] if text else "no website content"


def _fallback_profile(
    verdict: dict[str, Any],
    smart_data: dict[str, Any],
    query_context: dict[str, Any],
) -> dict[str, str]:
    name = str(verdict.get("company") or "")
    role = str(verdict.get("role") or "Other")
    industry = str(query_context.get("industry") or "market")
    country = str(query_context.get("country") or "global")
    fn = str(verdict.get("company_function") or "").replace("_", " ").strip()
    conf = float(verdict.get("confidence") or 0)
    sig = verdict.get("signals") or {}
    kws = [str(k) for k in (sig.get("keywords") or []) if len(str(k)) >= 4][:12]
    products = "; ".join(kws[:8]) if kws else "Not extracted from website"

    desc = extract_company_summary(smart_data, max_len=900)
    if not desc:
        desc = str(verdict.get("company_summary") or "")[:900]
    if not desc or len(desc) < 40:
        desc = f"{name} operates in {industry.lower()} (role: {role})."

    role_bits = [role]
    if fn and fn.lower() not in ("unknown", "vendor"):
        role_bits.append(fn)
    role_detail = (
        f"{' / '.join(role_bits)} in {industry}. "
        f"Participates in the market value chain as classified from website signals."
    )

    geo = country if country != "global" else "Global (target run)"
    if verdict.get("country_match"):
        geo = f"Signals match target geography ({country})"
    else:
        geo = f"Target geography: {geo}; verify operational presence on website"

    rationale_parts = [
        f"Included as {role} for {industry}",
        f"classification confidence {conf:.0%}",
    ]
    if verdict.get("is_seed"):
        rationale_parts.append("Phase 1 seed company for this market")
    if sig.get("industry_match"):
        rationale_parts.append("website content matches market keywords")
    if verdict.get("quality_score"):
        rationale_parts.append(f"export quality score {verdict['quality_score']}")

    return {
        "parent_or_independent": "Not stated on website",
        "role_detail": role_detail,
        "product_system_types": products,
        "geographic_presence": geo,
        "company_description": desc,
        "inclusion_rationale": "; ".join(rationale_parts) + ".",
    }


def _normalize_profile(raw: dict[str, Any], fallback: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in (
        "parent_or_independent",
        "role_detail",
        "product_system_types",
        "geographic_presence",
        "company_description",
        "inclusion_rationale",
    ):
        val = str(raw.get(key) or "").strip()
        if not val or val.lower() in ("n/a", "null", "none"):
            val = fallback[key]
        out[key] = val[:2500] if key != "product_system_types" else val[:1800]
    return out


def synthesize_export_profile_sync(
    verdict: dict[str, Any],
    smart_data: dict[str, Any],
    query_context: dict[str, Any],
    *,
    scope: dict[str, Any] | None = None,
    settings: Settings | None = None,
    client: Any | None = None,
) -> dict[str, str]:
    """Blocking LLM profile synthesis (run via asyncio.to_thread)."""
    fallback = _fallback_profile(verdict, smart_data, query_context)
    settings = settings or Settings.load()
    if client is None:
        from vendor_intel.clients.claude import ClaudeClient

        client = ClaudeClient(settings)

    if not client.available:
        return fallback

    crawl = _crawl_text(smart_data, verdict)
    user = json.dumps(
        {
            "company": {
                "name": verdict.get("company"),
                "domain": verdict.get("domain"),
                "role": verdict.get("role"),
                "company_function": verdict.get("company_function"),
                "is_seed": verdict.get("is_seed"),
            },
            "query_context": {
                "industry": query_context.get("industry"),
                "country": query_context.get("country"),
                "market": (scope or {}).get("market"),
                "industry_terms": ((scope or {}).get("industry_terms") or [])[:8],
            },
            "classification": {
                "confidence": verdict.get("confidence"),
                "quality_score": verdict.get("quality_score"),
                "country_match": verdict.get("country_match"),
                "signals": verdict.get("signals"),
            },
            "website_excerpt": crawl,
        },
        indent=2,
    )[:6500]

    try:
        raw = client.complete_json(_PROFILE_SYSTEM, user, max_tokens=700)
        out = raw if isinstance(raw, dict) else {}
        profile = _normalize_profile(out, fallback)
        from vendor_intel.pipeline.llm_meter import get_meter

        get_meter().add_profile()
        return profile
    except Exception:
        return fallback


async def synthesize_export_profile(
    verdict: dict[str, Any],
    smart_data: dict[str, Any],
    query_context: dict[str, Any],
    *,
    scope: dict[str, Any] | None = None,
    settings: Settings | None = None,
    client: Any | None = None,
) -> dict[str, str]:
    return await asyncio.to_thread(
        synthesize_export_profile_sync,
        verdict,
        smart_data,
        query_context,
        scope=scope,
        settings=settings,
        client=client,
    )


async def enrich_export_profiles(
    rows: list[dict[str, Any]],
    enriched: dict[str, Any],
    query_context: dict[str, Any],
    *,
    scope: dict[str, Any] | None = None,
    settings: Settings | None = None,
    client: Any | None = None,
) -> list[dict[str, Any]]:
    """Attach rich profile fields to each exported row (parallel LLM with progress)."""
    if not rows:
        return rows

    settings = settings or Settings.load()
    if client is None:
        from vendor_intel.clients.claude import ClaudeClient

        client = ClaudeClient(settings)

    total = len(rows)
    workers = _PROFILE_WORKERS
    print(
        f"  [pipeline] Phase 5 — export profiles ({total} companies, "
        f"{workers} parallel, ~{_PROFILE_TIMEOUT_S:.0f}s timeout each)",
        flush=True,
    )

    sem = asyncio.Semaphore(workers)
    results: list[dict[str, Any] | None] = [None] * total

    async def _one(idx: int, row: dict[str, Any]) -> None:
        name = str(row.get("company") or "")
        dom = str(row.get("domain") or "")
        smart = enriched.get(name) or enriched.get(dom) or {}
        print(f"  [profile] {idx}/{total} {name[:42]} …", flush=True)
        t0 = time.perf_counter()
        try:
            profile = await asyncio.wait_for(
                synthesize_export_profile(
                    row,
                    smart,
                    query_context,
                    scope=scope,
                    settings=settings,
                    client=client,
                ),
                timeout=_PROFILE_TIMEOUT_S,
            )
            tag = "OK"
        except asyncio.TimeoutError:
            profile = _fallback_profile(row, smart, query_context)
            tag = "timeout→fallback"
        except Exception as exc:
            profile = _fallback_profile(row, smart, query_context)
            tag = f"fallback ({type(exc).__name__})"
        elapsed = time.perf_counter() - t0
        results[idx - 1] = {**row, **profile}
        print(f"  [profile] {idx}/{total} {name[:42]} — {tag} ({elapsed:.0f}s)", flush=True)

    async def _run_limited(idx: int, row: dict[str, Any]) -> None:
        async with sem:
            await _one(idx, row)

    await asyncio.gather(*(_run_limited(i, row) for i, row in enumerate(rows, 1)))
    return [r for r in results if r is not None]
