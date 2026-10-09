"""Validate configuration before a live (non-mock) pipeline run."""
from __future__ import annotations

from vendor_intel.clients.duckduckgo import duckduckgo_available
from vendor_intel.config import Settings
from vendor_intel.mock.fixtures import is_mock_run


class LiveConfigError(Exception):
    """Raised when live mode is requested but requirements are missing."""


def validate_live_settings(settings: Settings) -> list[str]:
    """Return warnings; raise LiveConfigError on blocking issues."""
    if is_mock_run(settings):
        return []

    from vendor_intel.placeholders.load_keys import apply_env_overrides

    apply_env_overrides()

    errors: list[str] = []
    warnings: list[str] = []

    from vendor_intel.placeholders import llm as llm_ph

    if not llm_ph.is_configured():
        errors.append(
            "LLM API key is missing. Set ANTHROPIC_API_KEY, GEMINI_API_KEY, GROQ_API_KEY, "
            "or OPENCODE_API_KEY (see LIVE_SETUP.md). Or run with --mock for demo mode."
        )
    elif llm_ph.LLM_PROVIDER == "opencode":
        warnings.append(
            f"LLM: OpenCode Zen model '{llm_ph.OPENCODE_MODEL}' "
            "(free options: deepseek-v4-flash-free, nemotron-3-super-free, big-pickle)"
        )

    from vendor_intel.clients.search_router import skip_ddgs

    if not duckduckgo_available() and not skip_ddgs():
        warnings.append(
            "ddgs package not installed — pip install ddgs. "
            "Or set SKIP_DDGS=true and use Bing HTML / SearXNG."
        )

    if settings.search_backup == "searxng" and settings.searxng_base_url:
        from vendor_intel.clients.host_reachability import searxng_local_reachable

        if not searxng_local_reachable():
            warnings.append(
                f"SearXNG not running at {settings.searxng_base_url} — "
                "run: docker compose up -d"
            )

    if skip_ddgs():
        warnings.append(
            "SKIP_DDGS=true — ddgs disabled; router fallbacks only (Bing HTML / SearXNG)."
        )
    else:
        import os

        from vendor_intel.clients.proxy_pool import (
            default_cache_path,
            load_verified_pool,
            proxy_pool_enabled,
        )

        if proxy_pool_enabled():

            if not load_verified_pool():
                warnings.append(
                    "DDGS_USE_PROXY_POOL=true but cache empty — run: "
                    "scripts/check_proxies.py  (or set DDGS_AUTO_PROXY=true)"
                )
            else:
                n = len(load_verified_pool())
                warnings.append(
                    f"Search proxies: {n} verified in {default_cache_path().name}"
                )

    if not settings.web_fetch_enabled:
        warnings.append("WEB_FETCH_ENABLED=false — website scraping disabled.")
    elif not is_mock_run(settings):
        backend = (settings.scrape_backend or "ddgs").strip().lower()
        if not duckduckgo_available():
            errors.append("WEB_FETCH_ENABLED=true requires ddgs: pip install -U ddgs")
        if backend in ("selenium", "ddgs_then_selenium"):
            try:
                import selenium  # noqa: F401
            except ImportError:
                errors.append(
                    f"SCRAPE_BACKEND={backend} requires selenium: pip install selenium"
                )
            warnings.append(
                "Scrape fallback: Selenium + Chrome "
                f"(headless={settings.selenium_headless})"
            )
        fmt = getattr(settings, "scrape_extract_fmt", "text_markdown")
        warnings.append(
            f"Website scrape: ddgs.extract() (SCRAPE_BACKEND={backend}, fmt={fmt})"
        )

    if settings.google_alerts_enabled:
        warnings.append(
            "Google Alerts enabled — set GOOGLE_ALERTS_RSS_URLS or run scripts/run_alerts_worker.py."
        )

    if not is_mock_run(settings):
        try:
            from vendor_intel.utils.domain_corrections import hostname_resolves

            probes = ("www.braskem.com.br", "google.com")
            if not any(hostname_resolves(h) for h in probes):
                warnings.append(
                    "DNS: cannot resolve test hosts (braskem.com.br / google.com). "
                    "Crawls will fail locally — try Google DNS 8.8.8.8 or use ddgs.extract fallback."
                )
        except Exception:
            pass

    if errors:
        raise LiveConfigError("\n".join(f"  - {e}" for e in errors))
    return warnings


def print_run_banner(settings: Settings, warnings: list[str]) -> None:
    mode = "MOCK (demo)" if is_mock_run(settings) else "LIVE"
    print(f"\n=== Vendor Intelligence — {mode} ===")
    try:
        from vendor_intel.config import Settings

        s = Settings.load()
        prof = getattr(s, "pipeline_profile", "quality")
        print(f"  Pipeline profile: {prof}")
        if s.pipeline_recall_mode:
            print("  Mode: RECALL (broad export, noise OK)")
        elif prof in ("quality", "balanced"):
            print("  Mode: QUALITY (entity gate + export filter, ~25-65 rows)")
        if getattr(s, "pipeline_use_ssc", True) and prof != "deep":
            print("  Enrichment: SSC (server-side content, fast)")
    except Exception:
        pass
    if is_mock_run(settings):
        print("  Data: hardcoded demo companies (no search/API).")
        print("  To go live: set USE_MOCK_DATA=false in .env — see LIVE_SETUP.md")
    else:
        from vendor_intel.clients.search_router import search_stack_description

        print(f"  Search: {search_stack_description(settings)}")
        print("  Fallback: Wikipedia when web search is thin")
        print(
            f"  LLM: {settings.llm_provider} "
            f"(~1 compile + ~1 classify per company; see run summary)"
        )
        if settings.web_fetch_enabled:
            backend = (settings.scrape_backend or "ddgs").strip().lower()
            fmt = getattr(settings, "scrape_extract_fmt", "text_markdown")
            print(f"  Scrape: ddgs.extract ({fmt}) [backend={backend}]")
        else:
            print("  Scrape: OFF (WEB_FETCH_ENABLED=false)")
        if settings.google_alerts_enabled:
            print("  News: web search + Google Alerts articles")
        else:
            print("  News: web search (DDGS/Bing)")
    for w in warnings:
        print(f"  Warning: {w}")
    print()
