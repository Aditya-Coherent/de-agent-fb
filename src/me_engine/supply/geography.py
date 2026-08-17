"""Own geography scoping for the supply-side pipeline's bottom-up estimation
technique. Proposes a standard global country/region breakdown for a market
via one LLM call, so this never has to read the Desk Research pipeline's
generated files to know which countries to estimate.
"""
from __future__ import annotations

from pathlib import Path

from ..generation.config import GenConfig
from ..generation.llm import GenLLM

_GEOGRAPHY_SYSTEM = (
    "You are a market-research analyst. Given a market name, propose a standard global "
    "geography breakdown suitable for market-sizing: major individual countries where this "
    "market has meaningful activity, plus 'Rest of <region>' buckets so the whole world is "
    "covered without an excessive number of entries. Respond ONLY with a JSON object: "
    '{"countries": ["<country or region>", ...]} with between 15 and 25 entries.'
)


def discover_countries(market_name: str, config: GenConfig, cache_path: Path | None = None) -> list[str]:
    """Own LLM call — does not read any Desk Research output."""
    llm = GenLLM(config, cache_path=cache_path)
    data = llm.complete_json(_GEOGRAPHY_SYSTEM, f"Market: {market_name}")
    return [str(c).strip() for c in (data.get("countries") or []) if str(c).strip()]
