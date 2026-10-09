"""Suggest Google Alert queries from Phase 1 market scope — copy-paste into google.com/alerts."""
from __future__ import annotations

import re
from typing import Any

_LISTICLE = re.compile(
    r"\b(?:top|best|list|ranking|largest|leading\s+\d+)\b",
    re.I,
)
_GENERIC = frozenset(
    {
        "market",
        "global",
        "company",
        "companies",
        "official",
        "website",
        "digital",
        "system",
        "systems",
        "technology",
        "solutions",
        "services",
    }
)


def _clean_phrase(text: str, *, max_words: int = 8) -> str:
    s = re.sub(r"\s+", " ", (text or "").strip())
    s = re.sub(r"[^\w\s\-&]", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    words = s.split()[:max_words]
    return " ".join(words)


def _alert_ok(query: str) -> bool:
    q = query.strip()
    if len(q) < 8 or len(q) > 120:
        return False
    if _LISTICLE.search(q):
        return False
    if q.lower().count(" ") < 1:
        return False
    return True


def _add(
    out: list[dict[str, str]],
    seen: set[str],
    query: str,
    reason: str,
    *,
    max_items: int,
) -> None:
    q = _clean_phrase(query)
    key = q.lower()
    if not q or key in seen or not _alert_ok(q):
        return
    seen.add(key)
    out.append({"query": q, "reason": reason})
    if len(out) >= max_items:
        return


def _scope_from_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    scope = manifest.get("scope")
    return scope if isinstance(scope, dict) else {}


def _segment_phrases(scope: dict[str, Any], *, max_items: int = 6) -> list[str]:
    phrases: list[str] = []
    for layer in scope.get("value_chain_layers") or []:
        if not isinstance(layer, dict):
            continue
        for seg in layer.get("segments") or []:
            if not isinstance(seg, dict):
                continue
            name = str(seg.get("segment_name") or "").strip()
            if name:
                phrases.append(name)
            for sub in (seg.get("sub_segments") or [])[:2]:
                s = str(sub or "").strip()
                if s and len(s) >= 4:
                    phrases.append(s)
        if len(phrases) >= max_items:
            break
    return phrases[:max_items]


def suggest_google_alert_queries(
    manifest: dict[str, Any],
    *,
    max_alerts: int = 6,
) -> list[dict[str, str]]:
    """
    Build copy-paste Google Alert queries from Phase 1 JSON (test_phase1 or phase1 plan).
    """
    scope = _scope_from_manifest(manifest)
    query = str(manifest.get("query") or scope.get("market") or "").strip()
    market = str(scope.get("market") or query).strip()
    geo = str((scope.get("geographies") or ["global"])[0] or "global").strip()
    geo_suffix = "" if geo.lower() in ("global", "worldwide") else f" {geo}"

    include = list(scope.get("include_keywords") or [])
    industry = list(scope.get("industry_terms") or [])
    eco = list(scope.get("ecosystem_functions") or [])
    segments = _segment_phrases(scope)

    out: list[dict[str, str]] = []
    seen: set[str] = set()

    market_short = _clean_phrase(market, max_words=5)
    if market_short:
        _add(
            out,
            seen,
            f"{market_short} company{geo_suffix}",
            "Broad market — new vendors and announcements",
            max_items=max_alerts,
        )
        _add(
            out,
            seen,
            f"{market_short} manufacturer{geo_suffix}",
            "Hardware / product manufacturers",
            max_items=max_alerts,
        )

    for kw in include:
        if len(out) >= max_alerts:
            break
        k = _clean_phrase(str(kw), max_words=5)
        if not k or k.lower() in _GENERIC:
            continue
        if market_short and market_short.lower() not in k.lower():
            k = _clean_phrase(f"{market_short} {k}", max_words=7)
        if len(k) < 8:
            continue
        _add(
            out,
            seen,
            f"{k}{geo_suffix}",
            "From Phase 1 include_keywords",
            max_items=max_alerts,
        )

    for term in industry[:4]:
        if len(out) >= max_alerts:
            break
        t = _clean_phrase(str(term), max_words=5)
        if not t or t.lower() in _GENERIC:
            continue
        _add(
            out,
            seen,
            f"{t} vendor{geo_suffix}",
            "From Phase 1 industry_terms",
            max_items=max_alerts,
        )

    for role in eco[:4]:
        if len(out) >= max_alerts:
            break
        r = _clean_phrase(str(role), max_words=5)
        if not r:
            continue
        _add(
            out,
            seen,
            f"{r}{geo_suffix}",
            "From Phase 1 ecosystem role",
            max_items=max_alerts,
        )

    for seg in segments[:3]:
        if len(out) >= max_alerts:
            break
        s = _clean_phrase(seg, max_words=5)
        if not s:
            continue
        _add(
            out,
            seen,
            f"{s} company{geo_suffix}",
            "From value-chain segment",
            max_items=max_alerts,
        )

    return out[:max_alerts]


def format_alerts_report(
    manifest: dict[str, Any],
    suggestions: list[dict[str, str]],
) -> str:
    scope = _scope_from_manifest(manifest)
    query = str(manifest.get("query") or "")
    market = str(scope.get("market") or query)
    geo = str((scope.get("geographies") or ["global"])[0])

    lines = [
        "# Google Alerts — suggested queries",
        "",
        f"**Market query:** {query}",
        f"**Market:** {market}",
        f"**Geography:** {geo}",
        "",
        "Create each alert at https://www.google.com/alerts",
        "",
        "**Settings (recommended for all):**",
        "- Sources: Automatic",
        "- Language: English (or your market language)",
        "- Region: Any region (or your target country)",
        "- How often: Once a day (or As-it-happens)",
        "- Deliver to: **RSS feed** (click RSS icon → copy URL)",
        "",
        "---",
        "",
        "## Copy-paste alert queries",
        "",
    ]
    for i, row in enumerate(suggestions, 1):
        lines.append(f"### {i}. `{row['query']}`")
        lines.append(f"- *Why:* {row['reason']}")
        lines.append("")

    lines.extend(
        [
            "---",
            "",
            "## After creating alerts",
            "",
            "1. Copy all RSS feed URLs from Google Alerts",
            "2. Add to `.env` (comma-separated, no spaces after commas):",
            "",
            "```env",
            "GOOGLE_ALERTS_ENABLED=true",
            "GOOGLE_ALERTS_RSS_URLS=https://www.google.com/alerts/feeds/...,https://www.google.com/alerts/feeds/...",
            "```",
            "",
            "3. Refresh the local article store (run before each pipeline):",
            "",
            "```powershell",
            ".venv\\Scripts\\python.exe scripts\\run_alerts_worker.py --no-browser",
            "```",
            "",
            "4. Run the full pipeline:",
            "",
            "```powershell",
            ".venv\\Scripts\\python.exe run_query.py --query \"YOUR MARKET\" --country global",
            "```",
            "",
            "*You only add new RSS URLs when entering a new industry. Re-run the worker before every pipeline.*",
            "",
        ]
    )
    return "\n".join(lines)
