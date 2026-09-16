"""FastAPI route for the standalone "Segmentation Demo" UI tab.

Thin adapter over `me_engine.segmentation_demo.generate_segmentation_demo`
(unchanged pipeline logic lives there) -- a single GET endpoint, since this
is a one-shot generate-and-render demo, not a multi-phase human-approved
pipeline like Desk Research / Demand-Usage / Demand-Usage-India. Included
into `main.py` via `app.include_router(...)`; touches no other route/logic.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from me_engine.generation.config import GenConfig
from me_engine.segmentation_demo import generate_segmentation_demo
from me_engine.trade_evidence import fetch_country_trade_evidence
from me_engine.demand_calculator import COUNTRY_DATA, PARAMS, build_leaf
from me_engine.apparent_consumption import SUPPORTED_COUNTRIES, build_apparent_consumption

ROOT = Path(__file__).resolve().parents[1]
CACHE_PATH = ROOT / "output" / ".gen_cache.db"

router = APIRouter(prefix="/api/segmentation-demo", tags=["segmentation-demo"])


@router.get("/generate")
def generate(market: str = "Vinegar") -> dict:
    market = market.strip()
    if not market:
        raise HTTPException(status_code=400, detail="market is required")
    cfg = GenConfig.load(ROOT)
    if not cfg.is_online:
        raise HTTPException(status_code=503, detail="No LLM API key found in .env (DEEPSEEK_API_KEY / OPENAI_API_KEY).")
    try:
        return generate_segmentation_demo(market, cfg, cache_path=CACHE_PATH)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Segmentation demo generation failed: {exc}") from exc


@router.get("/trade-evidence")
def trade_evidence(country: str = "India", year: int = 2023) -> dict:
    """Official UN Comtrade customs statistics for one country -- the
    source-backed counterpart to the LLM's price estimates. Never raises on a
    failed lookup: returns `available: false` so the UI shows an honest 'no
    evidence retrieved' state instead of a fabricated figure."""
    return fetch_country_trade_evidence(country.strip() or "India", year=year, root=ROOT)


class CalcBody(BaseModel):
    country: str = "India"
    product: str = "Balsamic Vinegar"
    application: str = "Food and Beverages"
    # The affinity prior is the ONLY judgement input; passed in so the caller
    # can override it and watch the whole chain recompute.
    affinity: dict[str, dict[str, float]]


@router.post("/calculate")
def calculate(body: CalcBody) -> dict:
    """Compute one leaf number from the deterministic demand build-up and
    return the entire driver chain, each step provenance-tagged. No LLM is
    consulted for any magnitude here."""
    try:
        return build_leaf(body.country, body.product, body.application, body.affinity, root=ROOT)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/apparent-consumption")
def apparent_consumption(market: str = "Vinegar", country: str = "Italy", year: int = 2023) -> dict:
    """Apparent consumption for any market/country, plus the Product ->
    Application tree beneath it. Trade is real customs data; production is an
    estimate and is labelled as one."""
    cfg = GenConfig.load(ROOT)
    if not cfg.is_online:
        raise HTTPException(status_code=503, detail="No LLM API key found in .env.")
    try:
        return build_apparent_consumption(
            market.strip() or "Vinegar", country.strip() or "Italy", cfg,
            year=year, cache_path=CACHE_PATH, root=ROOT,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Apparent-consumption build failed: {exc}") from exc


@router.get("/calculator-config")
def calculator_config() -> dict:
    """The model's tunable parameters and the countries it has reference data
    for -- exposed so the UI can show exactly what is assumed."""
    return {"parameters": PARAMS, "countries": sorted(COUNTRY_DATA.keys())}
