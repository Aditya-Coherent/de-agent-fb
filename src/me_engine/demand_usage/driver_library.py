"""Reusable demand-driver/conversion methodologies, keyed by usage
*semantics*, NOT by a fixed usage taxonomy -- usage buckets remain
market-specific (discovered fresh per market in `usage_decomposition_node`).
This library only stores the reusable pattern of HOW a usage bucket's demand
was measured, so the next market with a similar usage doesn't need the LLM
to re-derive a driver/conversion methodology from scratch -- and so a
newly-proposed methodology is visibly marked as new, never silently treated
as established fact.

Persisted as a flat JSON file (`output/demand_usage/driver_library.json`).
Nearest-neighbor lookup reuses the same embedding model + cosine-similarity
pattern already used for usage-bucket overlap detection in `agents.py`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_DEFAULT_PATH = Path("output/demand_usage/driver_library.json")
_MATCH_THRESHOLD = 0.80  # same order as agents.py's _OVERLAP_SIMILARITY_THRESHOLD


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))  # embed() already L2-normalises


def _load(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save(path: Path, entries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")


def lookup(usage_description: str, path: Path = _DEFAULT_PATH) -> dict[str, Any] | None:
    """Nearest-neighbor match against stored methodologies. Returns None if
    the library is empty or nothing clears the similarity bar -- callers
    must then let the LLM propose a fresh methodology."""
    entries = _load(path)
    if not entries:
        return None
    try:
        from me_engine.generation.semantic_search import embed
    except Exception:  # noqa: BLE001
        return None

    try:
        query_vec = embed(usage_description)
    except Exception:  # noqa: BLE001
        return None

    best, best_sim = None, 0.0
    for e in entries:
        vec = e.get("_embedding")
        if not vec:
            continue
        sim = _cosine(query_vec, vec)
        if sim > best_sim:
            best, best_sim = e, sim
    if best is not None and best_sim >= _MATCH_THRESHOLD:
        return {k: v for k, v in best.items() if k != "_embedding"} | {"match_similarity": round(best_sim, 4)}
    return None


def append(
    usage_description: str,
    driver_name: str,
    driver_unit: str,
    consumption_unit: str,
    conversion_formula: str,
    required_evidence: list[str],
    market_name: str,
    path: Path = _DEFAULT_PATH,
    intensity_kind: str = "rate_per_driver_unit",
    unit_contract_status: str = "unknown",
) -> None:
    """Persist a newly-LLM-proposed methodology, tagged `newly_derived`. Never
    called for a methodology that came from `lookup()` -- reused entries just
    get their `markets_seen` list extended by the caller if desired.

    `unit_contract_status` is informational only (computed once, at save time,
    by `units.py` via the caller) -- it is NOT re-validated on reuse. The
    authoritative gate is always `calculate_node`'s own fresh check on every
    run, so a stale or wrong cached verdict here can never bypass it."""
    entries = _load(path)
    embedding: list[float] | None = None
    try:
        from me_engine.generation.semantic_search import embed
        embedding = embed(usage_description)
    except Exception:  # noqa: BLE001
        pass

    entries.append({
        "usage_key_text": usage_description,
        "driver_name": driver_name,
        "driver_unit": driver_unit,
        "consumption_unit": consumption_unit,
        "conversion_formula": conversion_formula,
        "required_evidence": required_evidence,
        "intensity_kind": intensity_kind,
        "unit_contract_status": unit_contract_status,
        "source": "newly_derived",
        "markets_seen": [market_name],
        "_embedding": embedding,
    })
    _save(path, entries)
