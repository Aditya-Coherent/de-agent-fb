"""Reusable demand-driver/conversion methodologies, keyed by usage
*semantics*, NOT by a fixed usage taxonomy -- usage buckets remain
market-specific (discovered fresh per market in `usage_decomposition_node`).
This library only stores the reusable pattern of HOW a usage bucket's demand
was measured, so the next market with a similar usage doesn't need the LLM
to re-derive a driver/conversion methodology from scratch -- and so a
newly-proposed methodology is visibly marked as new, never silently treated
as established fact.

Persisted as a flat JSON file (`output/demand_country_2a/driver_library.json`;
2A's own library, separate from 2B's `output/demand_usage/driver_library.json`).
Nearest-neighbor lookup reuses the same embedding model + cosine-similarity
pattern already used for usage-bucket overlap detection in `agents.py`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_DEFAULT_PATH = Path("output/demand_country_2a/driver_library.json")
_MATCH_THRESHOLD = 0.80  # same order as agents.py's _OVERLAP_SIMILARITY_THRESHOLD

# Correctness-audit fix (2026-08-26, `CEMENT_FORENSIC_AUDIT.md` finding A-2):
# Residential Construction, Commercial and Institutional Construction, and
# Infrastructure and Heavy Civil Engineering all reused the SAME
# "Residential construction starts" driver at cosine similarities (0.82,
# 0.86) above `_MATCH_THRESHOLD` -- housing starts is not a plausible
# physical proxy for roads/bridges/dams or general commercial buildings.
# Per the task's explicit instruction, the FIX IS NOT to raise/lower
# `_MATCH_THRESHOLD` -- it's a second, independent, deterministic check:
# do the query bucket and the candidate library entry belong to the SAME
# broad physical-use SCOPE? Generic construction/industrial-use vocabulary
# (not cement-specific), grouped so a match is legitimate reuse only when
# both sides share a scope, or neither side names one at all (a genuinely
# scope-agnostic driver, e.g. "global population").
_SCOPE_GROUPS: dict[str, frozenset[str]] = {
    "residential": frozenset({"residential", "housing", "home", "homes", "apartment", "apartments", "dwelling", "dwellings", "household", "households"}),
    "commercial_institutional": frozenset({"commercial", "institutional", "office", "offices", "retail", "hospital", "hospitals", "school", "schools"}),
    "infrastructure": frozenset({"infrastructure", "civil", "road", "roads", "bridge", "bridges", "tunnel", "tunnels", "airport", "airports", "port", "ports", "highway", "highways", "dam", "dams", "railway", "railways"}),
    "industrial": frozenset({"industrial", "factory", "factories", "manufacturing", "plant", "plants", "warehouse", "warehouses"}),
    "agricultural": frozenset({"agricultural", "agriculture", "farm", "farms", "farming"}),
}


def _scopes_of(text: str) -> set[str]:
    words = set(re.findall(r"[a-z]+", (text or "").lower()))
    return {group for group, vocab in _SCOPE_GROUPS.items() if words & vocab}


def _scope_conflict(query_description: str, candidate_description: str) -> bool:
    """True only when BOTH sides name at least one scope group AND those
    groups are entirely disjoint -- e.g. query names only 'infrastructure'
    and the candidate names only 'residential'. A side that names NO scope
    group (or a side that shares even one group with the other) is never
    treated as a conflict -- this must never reject a genuinely
    scope-agnostic or genuinely-shared-scope driver, only a clear mismatch."""
    q_scopes = _scopes_of(query_description)
    c_scopes = _scopes_of(candidate_description)
    if not q_scopes or not c_scopes:
        return False
    return q_scopes.isdisjoint(c_scopes)


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


def lookup(
    usage_description: str, path: Path = _DEFAULT_PATH, log: list[str] | None = None,
    market_name: str | None = None,
) -> dict[str, Any] | None:
    """Nearest-neighbor match against stored methodologies. Returns None if
    the library is empty, nothing clears the similarity bar, or the best
    embedding match fails the deterministic scope-compatibility check (see
    `_scope_conflict` -- correctness-audit fix, 2026-08-26) -- callers must
    then let the LLM propose a fresh methodology. `log`, when given, records
    WHY a high-similarity match was rejected (provenance for the decision,
    not just its outcome)."""
    entries = _load(path)
    # 2A fix: only reuse a driver first derived for THIS market. 2A's single
    # bucket description is one template for every market ("All final
    # consumption of X ..."), so cross-market embeddings clear the threshold
    # and _scope_conflict can't tell products apart -- seen: Laptops reusing
    # "National smartphone shipments", Televisions reusing "National watch
    # consumption volume".
    if market_name:
        wanted = market_name.strip().lower()
        entries = [e for e in entries if wanted in {str(m).strip().lower() for m in e.get("markets_seen") or []}]
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
    if best is None or best_sim < _MATCH_THRESHOLD:
        return None

    if _scope_conflict(usage_description, best.get("usage_key_text", "")):
        if log is not None:
            log.append(
                f"[driver_library] REJECTED reuse of '{best.get('driver_name')}' (embedding similarity "
                f"{best_sim:.4f}, above the {_MATCH_THRESHOLD} threshold) -- its stored usage description "
                f"belongs to a different physical-use scope than the current bucket's own description. "
                "Deriving a fresh driver instead of inheriting a mismatched one."
            )
        return None

    return {k: v for k, v in best.items() if k != "_embedding"} | {"match_similarity": round(best_sim, 4)}


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
    final_demand_unit: str = "",
) -> None:
    """Persist a newly-LLM-proposed methodology, tagged `newly_derived`. Never
    called for a methodology that came from `lookup()` -- reused entries just
    get their `markets_seen` list extended by the caller if desired.

    `unit_contract_status` is informational only (computed once, at save time,
    by `units.py` via the caller) -- it is NOT re-validated on reuse. The
    authoritative gate is always `calculate_node`'s own fresh check on every
    run, so a stale or wrong cached verdict here can never bypass it.

    `final_demand_unit` (correctness-audit fix, 2026-08-26,
    `CEMENT_FORENSIC_AUDIT.md` finding A-3/C): previously NOT persisted at
    all -- `lookup()`'s reuse path always fell back to `driver_unit`, which
    is only correct when `consumption_unit` is empty. Every reuse of an
    entry that DOES have a `consumption_unit` conversion silently inherited
    the wrong (pre-conversion) unit label. Now saved explicitly; a blank
    value is still handled safely by `lookup()`'s own fallback + the
    caller's `units.reconcile_final_demand_unit` reconciliation."""
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
        "final_demand_unit": final_demand_unit,
        "conversion_formula": conversion_formula,
        "required_evidence": required_evidence,
        "intensity_kind": intensity_kind,
        "unit_contract_status": unit_contract_status,
        "source": "newly_derived",
        "markets_seen": [market_name],
        "_embedding": embedding,
    })
    _save(path, entries)
