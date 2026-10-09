from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_yaml_config() -> dict[str, Any]:
    path = _project_root() / "config" / "default.yaml"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def _drop_env_overridden(model_cls: Any, overrides: dict[str, Any]) -> dict[str, Any]:
    """Remove YAML overrides for fields whose env var (validation alias) is explicitly set."""
    out: dict[str, Any] = {}
    fields = getattr(model_cls, "model_fields", {}) or {}
    for key, val in overrides.items():
        f = fields.get(key)
        alias = getattr(f, "validation_alias", None) if f is not None else None
        names = [alias] if isinstance(alias, str) else []
        names.append(key.upper())
        if any((os.getenv(n) or "").strip() for n in names):
            continue
        out[key] = val
    return out


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_project_root() / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM
    llm_provider: Literal[
        "anthropic", "deepseek", "gemini", "groq", "opencode", "openrouter", "mock"
    ] = Field(default="anthropic", validation_alias="LLM_PROVIDER")
    anthropic_api_key: str = Field(default="", validation_alias="ANTHROPIC_API_KEY")
    deepseek_api_key: str = Field(default="", validation_alias="DEEPSEEK_API_KEY")
    deepseek_model: str = Field(default="deepseek-chat", validation_alias="DEEPSEEK_MODEL")
    openrouter_api_key: str = Field(default="", validation_alias="OPENROUTER_API_KEY")
    openrouter_model: str = Field(
        default="deepseek/deepseek-chat-v3-0324", validation_alias="OPENROUTER_MODEL"
    )
    gemini_api_key: str = Field(default="", validation_alias="GEMINI_API_KEY")
    groq_api_key: str = Field(default="", validation_alias="GROQ_API_KEY")
    opencode_api_key: str = Field(default="", validation_alias="OPENCODE_API_KEY")
    opencode_model: str = Field(
        default="deepseek-v4-flash-free", validation_alias="OPENCODE_MODEL"
    )

    # Search
    serpapi_api_key: str = Field(default="", validation_alias="SERPAPI_API_KEY")
    serper_api_key: str = Field(default="", validation_alias="SERPER_API_KEY")
    brave_api_key: str = Field(default="", validation_alias="BRAVE_API_KEY")
    tavily_api_key: str = Field(default="", validation_alias="TAVILY_API_KEY")

    # News
    newsapi_api_key: str = Field(default="", validation_alias="NEWSAPI_API_KEY")
    gnews_api_key: str = Field(default="", validation_alias="GNEWS_API_KEY")

    # Enrichment & pages
    clearbit_api_key: str = Field(default="", validation_alias="CLEARBIT_API_KEY")
    firecrawl_api_key: str = Field(default="", validation_alias="FIRECRAWL_API_KEY")
    wikidata_enabled: bool = Field(default=True, validation_alias="WIKIDATA_ENABLED")
    web_fetch_enabled: bool = Field(default=True, validation_alias="WEB_FETCH_ENABLED")
    scrape_backend: str = Field(default="ddgs", validation_alias="SCRAPE_BACKEND")
    scrape_extract_fmt: str = Field(default="text_markdown", validation_alias="SCRAPE_EXTRACT_FMT")
    selenium_headless: bool = Field(default=True, validation_alias="SELENIUM_HEADLESS")
    selenium_page_load_timeout: float = Field(
        default=25.0, validation_alias="SELENIUM_PAGE_LOAD_TIMEOUT"
    )
    selenium_implicit_wait: float = Field(default=2.0, validation_alias="SELENIUM_IMPLICIT_WAIT")
    chrome_binary_path: str = Field(default="", validation_alias="CHROME_BINARY_PATH")

    # Run mode
    use_mock_data: bool = Field(default=True, validation_alias="USE_MOCK_DATA")
    mock_mode: bool = Field(default=False, validation_alias="MOCK_MODE")

    # Models (.env overrides yaml when COMPILER_MODEL / CLASSIFIER_MODEL set)
    compiler_model: str = Field(
        default="claude-haiku-4-5-20251001", validation_alias="COMPILER_MODEL"
    )
    batch_model: str = "claude-haiku-4-5-20251001"
    classifier_model: str = Field(
        default="claude-haiku-4-5-20251001", validation_alias="CLASSIFIER_MODEL"
    )
    market_map_model: str = Field(
        default="claude-sonnet-4-6", validation_alias="MARKET_MAP_MODEL"
    )
    gemini_model: str = "gemini-2.0-flash"
    groq_model: str = "llama-3.3-70b-versatile"

    # Quality thresholds
    min_final_count: int = Field(default=30, validation_alias="MIN_FINAL_COUNT")
    max_final_count: int = Field(default=150, validation_alias="MAX_FINAL_COUNT")
    target_solid_companies: int = Field(default=60, validation_alias="TARGET_SOLID_COMPANIES")
    min_distinct_domains_final: int = 3
    min_domains_per_gate: int = 1
    corporate_events_lookback_days: int = 7
    results_per_query: int = Field(default=25, validation_alias="RESULTS_PER_QUERY")
    target_unique_companies: int = Field(
        default=280, validation_alias="TARGET_UNIQUE_COMPANIES"
    )
    volume_prompt_count: int = Field(default=36, validation_alias="VOLUME_PROMPT_COUNT")
    widen_loop_max: int = Field(default=3, validation_alias="WIDEN_LOOP_MAX")
    widen_if_unique_lt: int = Field(default=250, validation_alias="WIDEN_IF_UNIQUE_LT")
    verify_top_candidates: int = Field(default=100, validation_alias="VERIFY_TOP_CANDIDATES")
    yield_stop_threshold: float = Field(default=0.05, validation_alias="YIELD_STOP_THRESHOLD")
    low_yield_consecutive: int = Field(default=10, validation_alias="LOW_YIELD_CONSECUTIVE")
    # Stop discovery when two consecutive parallel batches each add < this fraction of new unique
    # companies (0 = disabled; the default "fast" cap sets 0.03).
    discovery_batch_min_growth: float = Field(
        default=0.0, validation_alias="DISCOVERY_BATCH_MIN_GROWTH"
    )
    discovery_batch_stop_min_unique: int = Field(
        default=40, validation_alias="DISCOVERY_BATCH_STOP_MIN_UNIQUE"
    )
    discovery_yield_min_unique: int = Field(
        default=200, validation_alias="DISCOVERY_YIELD_MIN_UNIQUE"
    )
    discovery_force_widen_below: int = Field(
        default=100, validation_alias="DISCOVERY_FORCE_WIDEN_BELOW"
    )
    max_validation_entities: int = Field(default=60, validation_alias="MAX_VALIDATION_ENTITIES")
    phase3_fast_validation: bool = Field(default=True, validation_alias="PHASE3_FAST_VALIDATION")
    phase3_parallel_workers: int = Field(default=3, validation_alias="PHASE3_PARALLEL_WORKERS")
    phase3_max_hits_per_query: int = Field(default=3, validation_alias="PHASE3_MAX_HITS_PER_QUERY")
    phase3_agentic_validation: bool = Field(
        default=True, validation_alias="PHASE3_AGENTIC_VALIDATION"
    )
    phase3_agentic_max_entities: int = Field(
        default=25, validation_alias="PHASE3_AGENTIC_MAX_ENTITIES"
    )
    phase3_agentic_batch_size: int = Field(
        default=8, validation_alias="PHASE3_AGENTIC_BATCH_SIZE"
    )
    phase3_agentic_max_llm_calls: int = Field(
        default=3, validation_alias="PHASE3_AGENTIC_MAX_LLM_CALLS"
    )
    phase3_always_scrape_registry: bool = Field(
        default=True, validation_alias="PHASE3_ALWAYS_SCRAPE_REGISTRY"
    )

    csv_output_enabled: bool = Field(default=True, validation_alias="CSV_OUTPUT_ENABLED")
    csv_output_dir: str = Field(default="output", validation_alias="CSV_OUTPUT_DIR")

    # Free search (Phase 1)
    search_primary: str = Field(default="duckduckgo", validation_alias="SEARCH_PRIMARY")
    search_backup: str = Field(default="searxng", validation_alias="SEARCH_BACKUP")
    searxng_base_url: str = Field(default="http://127.0.0.1:8080", validation_alias="SEARXNG_BASE_URL")
    min_results_before_backup: int = Field(default=5, validation_alias="MIN_RESULTS_BEFORE_BACKUP")
    phase1_smoke_max_prompts: int = Field(
        default=4, validation_alias="PHASE1_SMOKE_MAX_PROMPTS"
    )
    phase1_global_smoke_max_prompts: int = Field(
        default=10, validation_alias="PHASE1_GLOBAL_SMOKE_MAX_PROMPTS"
    )
    phase1_global_discovery_prompts: int = Field(
        default=20, validation_alias="PHASE1_GLOBAL_DISCOVERY_PROMPTS"
    )
    pipeline_recall_mode: bool = Field(
        default=False, validation_alias="PIPELINE_RECALL_MODE"
    )
    pipeline_min_export_confidence: float = Field(
        default=0.55, validation_alias="PIPELINE_MIN_EXPORT_CONFIDENCE"
    )
    pipeline_profile: str = Field(
        default="quality", validation_alias="PIPELINE_PROFILE"
    )
    pipeline_use_ssc: bool = Field(default=True, validation_alias="PIPELINE_USE_SSC")
    pipeline_enumerate_players: bool = Field(
        default=True, validation_alias="PIPELINE_ENUMERATE_PLAYERS"
    )
    pipeline_directory_mining: bool = Field(
        default=True, validation_alias="PIPELINE_DIRECTORY_MINING"
    )
    pipeline_wikidata: bool = Field(
        default=True, validation_alias="PIPELINE_WIKIDATA"
    )
    pipeline_partner_mining: bool = Field(
        default=True, validation_alias="PIPELINE_PARTNER_MINING"
    )
    pipeline_market_roles: bool = Field(
        default=True, validation_alias="PIPELINE_MARKET_ROLES"
    )
    pipeline_strict_geo: bool = Field(
        default=True, validation_alias="PIPELINE_STRICT_GEO"
    )
    pipeline_shuffle_export: bool = Field(
        default=False, validation_alias="PIPELINE_SHUFFLE_EXPORT"
    )
    pipeline_enrich_concurrent: int = Field(default=12, validation_alias="PIPELINE_ENRICH_CONCURRENT")
    pipeline_classify_concurrent: int = Field(
        default=12, validation_alias="PIPELINE_CLASSIFY_CONCURRENT"
    )
    pipeline_discover_max: int = Field(default=250, validation_alias="PIPELINE_DISCOVER_MAX")
    pipeline_enrich_max: int = Field(default=250, validation_alias="PIPELINE_ENRICH_MAX")
    pipeline_export_min_rows: int = Field(default=0, validation_alias="PIPELINE_EXPORT_MIN_ROWS")
    pipeline_export_max_rows: int = Field(default=200, validation_alias="PIPELINE_EXPORT_MAX_ROWS")
    pipeline_global_discover_max: int = Field(
        default=300, validation_alias="PIPELINE_GLOBAL_DISCOVER_MAX"
    )
    pipeline_global_enrich_max: int = Field(
        default=300, validation_alias="PIPELINE_GLOBAL_ENRICH_MAX"
    )
    pipeline_global_export_min_rows: int = Field(
        default=0, validation_alias="PIPELINE_GLOBAL_EXPORT_MIN_ROWS"
    )
    pipeline_global_export_max_rows: int = Field(
        default=240, validation_alias="PIPELINE_GLOBAL_EXPORT_MAX_ROWS"
    )
    pipeline_global_min_export_confidence: float = Field(
        default=0.50, validation_alias="PIPELINE_GLOBAL_MIN_EXPORT_CONFIDENCE"
    )
    pipeline_global_min_quality: float = Field(
        default=0.48, validation_alias="PIPELINE_GLOBAL_MIN_QUALITY"
    )
    pipeline_global_volume_prompt_count: int = Field(
        default=26, validation_alias="PIPELINE_GLOBAL_VOLUME_PROMPT_COUNT"
    )
    # CHANGED: ddg worker pool
    ddg_worker_count: int = Field(default=0, validation_alias="DDG_WORKER_COUNT")
    ddg_request_delay_min: float = Field(default=3.0, validation_alias="DDG_REQUEST_DELAY_MIN")
    ddg_request_delay_max: float = Field(default=8.0, validation_alias="DDG_REQUEST_DELAY_MAX")

    # Google Alerts (Phase 3)
    google_alerts_enabled: bool = Field(default=False, validation_alias="GOOGLE_ALERTS_ENABLED")
    google_alerts_profile_path: str = Field(
        default="data/chrome-profile", validation_alias="GOOGLE_ALERTS_PROFILE_PATH"
    )
    google_alerts_headless: bool = Field(default=True, validation_alias="GOOGLE_ALERTS_HEADLESS")
    google_alerts_store_path: str = Field(
        default="data/alerts/articles.json", validation_alias="GOOGLE_ALERTS_STORE_PATH"
    )
    google_alerts_rss_urls: str = Field(
        default="", validation_alias="GOOGLE_ALERTS_RSS_URLS"
    )

    # RSS (optional)
    rss_feed_urls: str = Field(default="", validation_alias="RSS_FEED_URLS")

    # LLM cost cap
    max_llm_calls_per_run: int = Field(default=1, validation_alias="MAX_LLM_CALLS_PER_RUN")

    @classmethod
    def load(cls) -> "Settings":
        from dotenv import load_dotenv

        load_dotenv(_project_root() / ".env")
        yaml_cfg = load_yaml_config()
        research = yaml_cfg.get("research", {})
        evidence = yaml_cfg.get("evidence", {})
        freshness = yaml_cfg.get("freshness", {})
        llm = yaml_cfg.get("llm", {})
        search = yaml_cfg.get("search", {})
        validation = yaml_cfg.get("validation", {})
        csv_cfg = yaml_cfg.get("csv", {})

        overrides: dict[str, Any] = {}
        if yaml_cfg.get("phase1_smoke_max_prompts") is not None:
            overrides["phase1_smoke_max_prompts"] = int(yaml_cfg["phase1_smoke_max_prompts"])
        if research:
            overrides["min_final_count"] = research.get("min_final_count", 30)
            overrides["max_final_count"] = research.get("max_final_count", 60)
            overrides["target_solid_companies"] = research.get("target_solid_companies", 30)
        if evidence:
            overrides["min_distinct_domains_final"] = evidence.get(
                "min_distinct_domains_final", 3
            )
            overrides["min_domains_per_gate"] = evidence.get("min_domains_per_gate", 1)
        if freshness:
            overrides["corporate_events_lookback_days"] = freshness.get(
                "corporate_events_lookback_days", 7
            )
        if llm:
            if os.getenv("COMPILER_MODEL") is None:
                overrides["compiler_model"] = llm.get(
                    "compiler_model", overrides.get("compiler_model")
                )
            if os.getenv("CLASSIFIER_MODEL") is None:
                overrides["classifier_model"] = llm.get(
                    "classifier_model", overrides.get("classifier_model")
                )
            if os.getenv("MARKET_MAP_MODEL") is None:
                overrides["market_map_model"] = llm.get(
                    "market_map_model", overrides.get("market_map_model")
                )
            overrides["batch_model"] = llm.get("batch_model", overrides.get("batch_model"))
            overrides["gemini_model"] = llm.get("gemini_model", "gemini-2.0-flash")
            overrides["groq_model"] = llm.get("groq_model", "llama-3.3-70b-versatile")
        if search:
            overrides["results_per_query"] = search.get("results_per_query", 25)
            overrides["widen_if_unique_lt"] = search.get(
                "widen_if_unique_entities_lt", 80
            )
            if search.get("yield_stop_threshold") is not None:
                overrides["yield_stop_threshold"] = float(search["yield_stop_threshold"])
            if search.get("low_yield_consecutive") is not None:
                overrides["low_yield_consecutive"] = int(search["low_yield_consecutive"])
            if search.get("discovery_yield_min_unique") is not None:
                overrides["discovery_yield_min_unique"] = int(
                    search["discovery_yield_min_unique"]
                )
            # CHANGED: ddg worker pool — yaml defaults (env DDG_* wins at runtime)
            if search.get("ddg_worker_count") is not None:
                overrides["ddg_worker_count"] = int(search["ddg_worker_count"])
            if search.get("ddg_request_delay_min") is not None:
                overrides["ddg_request_delay_min"] = float(search["ddg_request_delay_min"])
            if search.get("ddg_request_delay_max") is not None:
                overrides["ddg_request_delay_max"] = float(search["ddg_request_delay_max"])
        disc = yaml_cfg.get("discovery", {})
        if disc:
            overrides["target_unique_companies"] = disc.get("target_unique_companies", 100)
            overrides["volume_prompt_count"] = disc.get("volume_prompt_count", 22)
            overrides["widen_loop_max"] = disc.get("widen_loop_max", 3)
            overrides["verify_top_candidates"] = disc.get("verify_top_candidates", 80)
            overrides["target_solid_companies"] = disc.get(
                "target_solid_companies",
                overrides.get("target_solid_companies", 30),
            )
            if search.get("primary"):
                overrides["search_primary"] = search["primary"]
            if search.get("backup"):
                overrides["search_backup"] = search["backup"]
            if search.get("searxng_base_url"):
                overrides["searxng_base_url"] = search["searxng_base_url"]
            if "min_results_before_backup" in search:
                overrides["min_results_before_backup"] = search["min_results_before_backup"]
        alerts_cfg = yaml_cfg.get("alerts", {})
        if alerts_cfg:
            # .env GOOGLE_ALERTS_ENABLED wins over config/default.yaml
            if "enabled" in alerts_cfg and os.getenv("GOOGLE_ALERTS_ENABLED") is None:
                overrides["google_alerts_enabled"] = bool(alerts_cfg["enabled"])
            if alerts_cfg.get("profile_path"):
                overrides["google_alerts_profile_path"] = alerts_cfg["profile_path"]
            if alerts_cfg.get("store_path"):
                overrides["google_alerts_store_path"] = alerts_cfg["store_path"]
        if llm and "max_calls_per_run" in llm:
            overrides["max_llm_calls_per_run"] = llm["max_calls_per_run"]
        if validation:
            overrides["max_validation_entities"] = validation.get("max_validation_entities", 60)
            if "fast_validation" in validation:
                overrides["phase3_fast_validation"] = bool(validation["fast_validation"])
            if "parallel_workers" in validation:
                overrides["phase3_parallel_workers"] = int(validation["parallel_workers"])
            if "max_hits_per_query" in validation:
                overrides["phase3_max_hits_per_query"] = int(validation["max_hits_per_query"])
            if "agentic_validation" in validation:
                overrides["phase3_agentic_validation"] = bool(validation["agentic_validation"])
            if validation.get("agentic_max_entities"):
                overrides["phase3_agentic_max_entities"] = int(validation["agentic_max_entities"])
            if validation.get("agentic_batch_size"):
                overrides["phase3_agentic_batch_size"] = int(validation["agentic_batch_size"])
            if validation.get("agentic_max_llm_calls"):
                overrides["phase3_agentic_max_llm_calls"] = int(validation["agentic_max_llm_calls"])
            if "always_scrape_registry" in validation:
                overrides["phase3_always_scrape_registry"] = bool(
                    validation["always_scrape_registry"]
                )
        if csv_cfg:
            if "enabled" in csv_cfg:
                overrides["csv_output_enabled"] = bool(csv_cfg["enabled"])
            if csv_cfg.get("output_dir"):
                overrides["csv_output_dir"] = csv_cfg["output_dir"]

        # Precedence: environment / .env  >  config/default.yaml  >  field defaults.
        # (Previously YAML was applied AFTER .env, so e.g. YIELD_STOP_THRESHOLD / WIDEN_LOOP_MAX /
        # TARGET_UNIQUE_COMPANIES in .env were silently overridden and early stopping never fired.)
        overrides = _drop_env_overridden(cls, overrides)

        base = cls()
        if overrides:
            base = base.model_copy(update=overrides)

        if _env_bool("MOCK_MODE", False):
            base = base.model_copy(update={"mock_mode": True})
        if os.getenv("USE_MOCK_DATA", "").lower() in ("0", "false", "no"):
            base = base.model_copy(update={"use_mock_data": False})

        return base
