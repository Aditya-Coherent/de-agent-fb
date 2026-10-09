from __future__ import annotations

import logging
import time
from typing import Any

from vendor_intel.clients.claude import ClaudeClient
from vendor_intel.config import Settings, _project_root
from vendor_intel.funnel.offline_compiler import build_offline_compiler_plan
from vendor_intel.funnel.query_intent import enrich_scope_from_query, parse_query_parts
from vendor_intel.funnel.scope_schema import normalize_run_scope, scope_summary
from vendor_intel.live_checks import LiveConfigError
from vendor_intel.mock.fixtures import MOCK_CONFIG_BASE, build_mock_compiler_config, is_mock_run
from vendor_intel.models import RunConfig

logger = logging.getLogger(__name__)

MOCK_CONFIG_DEFAULTS = {
    "evidence_policy": {"min_distinct_domains_final": 3, "min_domains_per_gate": 2},
    "freshness_policy": {"corporate_events_lookback_days": 7, "ma_cache_ttl_hours": 0},
}


def _as_config_dict(value: Any, default: dict[str, Any]) -> dict[str, Any]:
    """LLM sometimes returns gates/listing_rules as strings — coerce to dict."""
    if isinstance(value, dict) and value:
        return value
    if isinstance(value, dict):
        return dict(default)
    return dict(default)


def _load_compiler_system() -> str:
    path = _project_root() / "config" / "prompts" / "compiler_system.txt"
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return (
        "You are a market research query compiler. Output JSON only with scope, "
        "funnel_prompts, discovery_prompts, and defaults."
    )


def _load_minimal_compiler_system() -> str:
    path = _project_root() / "config" / "prompts" / "compiler_minimal.txt"
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return _load_compiler_system()


def _llm_failed(data: Any) -> bool:
    """Legacy check — prefer compiler_payload_usable after coerce_compiler_payload."""
    if not isinstance(data, dict):
        return True
    from vendor_intel.stages.compiler_coerce import compiler_payload_usable

    if compiler_payload_usable(data):
        return False
    if data.get("error") or data.get("status") == "llm_failed":
        return True
    scope = data.get("scope")
    if isinstance(scope, dict) and scope.get("market"):
        return False
    return True


def _llm_error_message(data: Any) -> str:
    if not isinstance(data, dict):
        return "invalid response"
    if data.get("error"):
        return str(data["error"])[:200]
    if data.get("raw_preview"):
        return str(data["raw_preview"])[:200]
    return "missing scope.market in JSON"


def _normalize_prompts(raw: Any, *, discovery_only: bool = False) -> list[dict[str, str]]:
    from vendor_intel.discovery.discovery_query_quality import (
        is_listicle_discovery_query,
        sanitize_discovery_query,
    )

    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for i, p in enumerate(raw, start=1):
        if not isinstance(p, dict) or not (p.get("text") or "").strip():
            continue
        pid = str(p.get("id", f"P{i}"))
        if discovery_only and pid in {"L0", "L1", "L2"}:
            continue
        text = sanitize_discovery_query(str(p["text"]).strip())
        if not text or is_listicle_discovery_query(text):
            continue
        out.append(
            {
                "id": pid,
                "level": str(p.get("level", "discovery" if pid.startswith("P") else pid)),
                "text": text,
            }
        )
    return out


def _merge_partial_llm(scope: dict[str, Any], data: dict[str, Any], query: str) -> tuple[dict, list, list]:
    """Use LLM scope fields even when prompts are incomplete."""
    llm_scope = data.get("scope") if isinstance(data.get("scope"), dict) else {}
    merged = {**scope, **llm_scope}
    merged["scope_source"] = (
        "llm_partial" if data.get("_salvaged") or data.get("status") == "llm_partial" else "llm"
    )
    funnel = _normalize_prompts(data.get("funnel_prompts") or [])
    discovery = _normalize_prompts(
        data.get("discovery_prompts") or data.get("prompts") or [],
        discovery_only=True,
    )
    merged = enrich_scope_from_query(merged, query)
    merged = normalize_run_scope(merged, query)
    return merged, funnel, discovery


def _resolve_compile_model(settings: Settings, claude: ClaudeClient, attempt: int) -> str | None:
    if settings.llm_provider != "opencode":
        return None
    from vendor_intel.placeholders.llm import opencode_models_for_attempt

    return opencode_models_for_attempt(attempt, claude._resolve_model())


def compile_query(query: str, claude: ClaudeClient, settings: Settings) -> RunConfig:
    if is_mock_run(settings):
        return build_mock_compiler_config(query)

    from vendor_intel.placeholders.load_keys import apply_env_overrides

    apply_env_overrides()

    if not claude.available:
        raise LiveConfigError(
            "Live mode requires an LLM API key in .env "
            "(OPENCODE_API_KEY, GEMINI_API_KEY, GROQ_API_KEY, or ANTHROPIC_API_KEY). "
            "See LIVE_SETUP.md."
        )

    from vendor_intel.funnel.market_understanding import (
        format_market_intel_for_compiler,
        merge_market_map_seeds,
        merge_market_understanding,
        understand_market,
    )
    from vendor_intel.funnel.query_intent import parse_query_parts

    market_pre, geo_pre = parse_query_parts(query)
    pre_scope = {
        "market": market_pre or query.strip(),
        "geographies": [geo_pre] if geo_pre else ["global"],
    }
    market_intel = understand_market(query, pre_scope, claude, settings)
    pre_scope = merge_market_understanding(pre_scope, market_intel)

    system_full = _load_compiler_system()
    system_minimal = _load_minimal_compiler_system()
    intel_block = format_market_intel_for_compiler(market_intel)
    user = (
        f"User query:\n{query}\n\n"
        f"Pre-analyzed market map (use for scope, ecosystem_functions, discovery_prompts, seeds):\n"
        f"{intel_block}\n"
    )
    user_minimal = (
        f"Query: {query}\n"
        f"Market context:\n{intel_block[:2500]}\n"
        f"Fill the JSON template for this market and geography."
    )

    from vendor_intel.placeholders.llm import opencode_model_chain
    from vendor_intel.stages.compiler_coerce import (
        coerce_compiler_payload,
        compiler_payload_usable,
        ensure_seed_companies,
    )

    data: Any = {"status": "llm_failed"}
    last_err = ""
    last_raw = ""
    consecutive_server_errors = 0

    if settings.llm_provider == "opencode":
        models_to_try = opencode_model_chain(claude._resolve_model())[:4]
    else:
        models_to_try = [None]

    compile_tries: list[tuple[bool, str | None]] = []
    for use_minimal in (False, True):
        for model in models_to_try:
            compile_tries.append((use_minimal, model))
    if not compile_tries:
        compile_tries = [(False, None), (True, None)]

    for call_idx, (use_minimal, model) in enumerate(compile_tries, start=1):
        if consecutive_server_errors >= 2:
            print("  [llm] Skipping further API calls after server errors", flush=True)
            break
        system = system_minimal if use_minimal else system_full
        prompt_user = user_minimal if use_minimal else user
        max_tok = 2048 if use_minimal else 4096
        if model:
            print(f"  [llm] Compile attempt {call_idx} model={model}", flush=True)

        raw_response = claude.complete_json(
            system, prompt_user, model=model, max_tokens=max_tok
        )
        if isinstance(raw_response, dict):
            last_raw = str(raw_response.get("_raw_text") or raw_response.get("raw_preview") or "")

        coerced = coerce_compiler_payload(raw_response, query, raw_text=last_raw or None)
        if compiler_payload_usable(coerced):
            data = coerced
            if coerced.get("status") in ("llm_partial", "llm_repaired"):
                print("  [llm] Repaired imperfect JSON — continuing with defaults", flush=True)
            break

        last_err = _llm_error_message(raw_response)
        if "http_401" in last_err or "model is disabled" in last_err.lower():
            print("  [llm] Model unavailable — trying next", flush=True)
            continue
        if "http_5" in last_err or "empty_response" in last_err:
            consecutive_server_errors += 1
        logger.warning("LLM compile attempt %s failed: %s", call_idx, last_err)
        time.sleep(0.5 if "http_5" not in last_err else 2.0)

    # Always coerce — fill scope.market, seeds, geo; never hard-fail on missing keys
    data = coerce_compiler_payload(
        data if isinstance(data, dict) else {},
        query,
        raw_text=last_raw or None,
    )
    scope = dict(data.get("scope") or {})
    scope = merge_market_understanding(scope, market_intel)
    status = str(data.get("status") or "llm_repaired")
    scope["scope_source"] = status if status.startswith("llm") else "llm_repaired"
    funnel_prompts = _normalize_prompts(data.get("funnel_prompts") or [])
    discovery_prompts = _normalize_prompts(
        data.get("discovery_prompts") or [],
        discovery_only=True,
    )
    ensure_seed_companies(scope, query)
    merge_market_map_seeds(scope, market_intel)

    if not funnel_prompts or not discovery_prompts:
        off_scope, off_funnel, off_disc = build_offline_compiler_plan(query, scope)
        scope = {**off_scope, **scope}
        funnel_prompts = funnel_prompts or off_funnel
        discovery_prompts = discovery_prompts or off_disc
        ensure_seed_companies(scope, query)
        print(
            f"  [llm] Merged offline prompts ({len(funnel_prompts)} funnel, "
            f"{len(discovery_prompts)} discovery)",
            flush=True,
        )

    from vendor_intel.funnel.discovery_prompts import build_discovery_prompts
    from vendor_intel.funnel.market_understanding import build_prompts_from_market_map
    from vendor_intel.pipeline.geo_limits import is_global_geography, pipeline_limits

    geo = (scope.get("geographies") or ["global"])[0]
    lim = pipeline_limits(settings, recall=False, country=str(geo))
    max_disc = int(lim["discovery_prompts"])
    if is_global_geography(str(geo)):
        max_disc = max(max_disc, int(lim.get("volume_prompts", max_disc) // 2))

    map_prompts = build_prompts_from_market_map(scope, query, geo=str(geo), max_prompts=26)
    discovery_prompts = build_discovery_prompts(
        scope,
        query,
        funnel_prompts,
        max_prompts=max_disc,
        llm_prompts=discovery_prompts,
        market_map_prompts=map_prompts,
    )

    if is_global_geography(str(geo)):
        from vendor_intel.discovery.volume_prompts import build_volume_prompts

        terms = scope.get("industry_terms") if isinstance(scope.get("industry_terms"), list) else []
        eco = scope.get("ecosystem_functions") if isinstance(scope.get("ecosystem_functions"), list) else []
        vol_extra = build_volume_prompts(
            str(scope.get("market") or ""),
            str(geo),
            industry_terms=terms,
            ecosystem_functions=eco,
            max_prompts=min(14, max_disc),
        )
        funnel_texts = {
            " ".join((fp.get("text") or "").lower().split()) for fp in funnel_prompts
        }
        seen: set[str] = set()
        merged: list[dict[str, str]] = []
        for p in discovery_prompts + vol_extra:
            text = str(p.get("text") or "").strip()
            key = " ".join(text.lower().split())
            if not text or key in seen or key in funnel_texts:
                continue
            seen.add(key)
            merged.append({**p, "text": text})
        for i, row in enumerate(merged[:max_disc]):
            row["id"] = f"P{i + 1}"
        discovery_prompts = merged[:max_disc]

    n_map = len(map_prompts)
    n_seeds = len(scope.get("seed_companies") or [])
    print(
        f"  [llm] Compiler ready ({settings.llm_provider} / {claude._resolve_model()}) — "
        f"{n_seeds} seeds, {len(funnel_prompts)} funnel + {len(discovery_prompts)} discovery "
        f"({n_map} from market map) [{scope.get('scope_source')}]",
        flush=True,
    )

    print(f"  [llm] Run scope: {scope_summary(scope)}", flush=True)

    raw = data if isinstance(data, dict) else {}
    gates = _as_config_dict(raw.get("gates"), MOCK_CONFIG_BASE["gates"])
    listing_rules = _as_config_dict(
        raw.get("listing_rules"), MOCK_CONFIG_BASE["listing_rules"]
    )
    ranking = _as_config_dict(raw.get("ranking"), MOCK_CONFIG_BASE.get("ranking", {}))
    evidence_policy = _as_config_dict(
        raw.get("evidence_policy"), MOCK_CONFIG_DEFAULTS["evidence_policy"]
    )
    freshness_policy = _as_config_dict(
        raw.get("freshness_policy"), MOCK_CONFIG_DEFAULTS["freshness_policy"]
    )
    if not isinstance(raw.get("gates"), dict):
        logger.warning(
            "LLM returned non-dict gates=%r — using defaults", raw.get("gates")
        )

    return RunConfig(
        scope=scope,
        funnel_prompts=funnel_prompts,
        prompts=discovery_prompts,
        gates=gates,
        listing_rules=listing_rules,
        ranking=ranking,
        evidence_policy=evidence_policy,
        freshness_policy=freshness_policy,
    )
