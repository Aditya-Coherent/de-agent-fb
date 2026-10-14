"""Consensus of published global market sizes for one market and base year.

Search -> read the full pages -> the LLM lists every GLOBAL market-size figure
with its year, publisher, growth rate and a verbatim quote -> code verifies
the number is in the quote, brings it to the base year with the source's own
CAGR (else the median CAGR of the other sources, else unchanged), keeps one
figure per publisher (the one closest to the base year), drops figures more
than 2x away from the median, and returns the median with its log-spread.
"""
from __future__ import annotations

import math
import re
import statistics
from typing import Any, Callable

from me_engine.generation.llm import GenLLM

OUTLIER_FACTOR = 2.0
SIGMA_FLOOR = 0.08      # publishers share methods and copy each other; never fully trust a tight cluster
SIGMA_SINGLE = 0.30     # one publisher only
YEARS_BACK = 3


def _num_in(value: float, quote: str) -> bool:
    nums = [float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*\.?\d*", quote or "")]
    return any(abs(n * s - value) <= 0.02 * max(value, 1e-9) for n in nums for s in (1.0, 1e-3, 1e3))


def gather(llm: GenLLM, market: str, base_year: int, search: Callable[[str], list[dict[str, str]]],
           pages: Callable[[str], list[str]]) -> dict[str, Any]:
    queries = [f"global {market} market size {base_year} USD billion",
               f"{market} market size {base_year - 1} valued at billion CAGR",
               f"{market} market value {base_year - 1} report revenue worldwide",
               f"{market} industry size {base_year} global revenue"]
    evidence: list[dict[str, str]] = []
    for q in queries:
        evidence += search(q)
    for url in list(dict.fromkeys(e["url"] for e in evidence))[:12]:
        evidence += [{"url": url, "text": f"[full page] {w}"[:1500]} for w in pages(url)]
    block = "\n".join(f"[{i}] ({e['url']}) {e['text']}" for i, e in enumerate(evidence))
    data = llm.complete_json(
        "You extract published market-size figures from web evidence. Use only the snippets; copy each "
        "sentence verbatim. Only GLOBAL (worldwide) figures for this exact market, not a region, segment or "
        "adjacent market. For a 'valued at X in Y, reaching Z by W' sentence, report X and Y (the actual, not the forecast).",
        f"Market: {market}\n\nSnippets:\n{block}\n\n"
        'Return JSON: {"figures": [{"value_usd_billion": <number>, "year": <int>, "publisher": "<research firm or site>", '
        '"cagr_pct": <number or null>, "quote": "<verbatim>", "snippet_index": <int>}]}',
    )
    figures = []
    for f in data.get("figures") or []:
        try:
            value, year = float(f.get("value_usd_billion")), int(f.get("year"))
        except (TypeError, ValueError):
            continue
        quote = str(f.get("quote") or "")
        idx = f.get("snippet_index")
        if value > 0 and _num_in(value, quote) and base_year - YEARS_BACK <= year <= base_year:
            cagr = f.get("cagr_pct")
            figures.append({"value_usd_mn": value * 1000, "year": year, "publisher": str(f.get("publisher") or "unknown"),
                            "cagr_pct": float(cagr) if isinstance(cagr, (int, float)) and -30 < cagr < 60 else None,
                            "quote": quote[:300],
                            "url": evidence[idx]["url"] if isinstance(idx, int) and 0 <= idx < len(evidence) else ""})
    return {"figures": figures, "n_snippets": len(evidence)}


def consensus(figures: list[dict[str, Any]], base_year: int) -> dict[str, Any] | None:
    """{value_usd_mn, sigma, n_publishers, used, dropped} or None without figures."""
    if not figures:
        return None
    cagrs = [f["cagr_pct"] for f in figures if f["cagr_pct"] is not None]
    fallback_cagr = statistics.median(cagrs) if cagrs else 0.0
    per_pub: dict[str, dict[str, Any]] = {}
    for f in figures:
        key = re.sub(r"[^a-z0-9]", "", f["publisher"].lower()) or f["url"]
        g = (f["cagr_pct"] if f["cagr_pct"] is not None else fallback_cagr) / 100
        norm = f["value_usd_mn"] * (1 + g) ** (base_year - f["year"])
        cand = {**f, "value_base_year_usd_mn": norm}
        if key not in per_pub or abs(base_year - f["year"]) < abs(base_year - per_pub[key]["year"]):
            per_pub[key] = cand
    values = [f["value_base_year_usd_mn"] for f in per_pub.values()]
    med = statistics.median(values)
    used = [f for f in per_pub.values() if med / OUTLIER_FACTOR <= f["value_base_year_usd_mn"] <= med * OUTLIER_FACTOR]
    dropped = [f for f in per_pub.values() if f not in used]
    logs = [math.log(f["value_base_year_usd_mn"]) for f in used]
    center = statistics.median(logs)
    if len(used) >= 2:
        mad = statistics.median(abs(x - center) for x in logs) * 1.4826
        sigma = max(SIGMA_FLOOR, mad / math.sqrt(len(used)), SIGMA_FLOOR)
        # a small sample can look tight by chance -- don't trust fewer than 3 publishers fully
        if len(used) < 3:
            sigma = max(sigma, 0.15)
    else:
        sigma = SIGMA_SINGLE
    return {"value_usd_mn": math.exp(center), "sigma": sigma, "n_publishers": len(used),
            "used": used, "dropped": dropped}
