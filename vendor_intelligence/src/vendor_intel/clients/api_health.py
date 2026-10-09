"""Probe configured APIs/services and report active vs inactive (Phase 1 / 2 manifests)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from vendor_intel.clients.duckduckgo import (
    configured_ddgs_backends,
    duckduckgo_available,
    duckduckgo_backend_name,
    duckduckgo_search,
)
from vendor_intel.clients.network_check import check_internet_dns
from vendor_intel.clients.searxng import searxng_ping
from vendor_intel.config import Settings
from vendor_intel.mock.fixtures import is_mock_run
from vendor_intel.placeholders import llm as llm_ph
from vendor_intel.scraping.fetch import SCRAPING_ENABLED, check_url_alive


def _status(ok: bool, *, skipped: bool = False) -> str:
    if skipped:
        return "skipped"
    return "active" if ok else "inactive"


async def check_all_apis(
    settings: Settings,
    *,
    llm_responded_ok: bool | None = None,
) -> dict[str, dict[str, Any]]:
    """
    Returns per-service health for manifest `api_status`.
    Does not call the LLM compiler again — pass llm_responded_ok from compile when available.
    """
    mock = is_mock_run(settings)
    out: dict[str, dict[str, Any]] = {}

    net_ok, net_err = check_internet_dns()
    out["internet_dns"] = {
        "status": _status(net_ok),
        "detail": net_err[:200] if net_err else "",
    }

    ddg_pkg = duckduckgo_backend_name()
    from vendor_intel.clients.duckduckgo import reset_network_search_state

    reset_network_search_state()
    ddg_installed = duckduckgo_available()
    ddg_active = False
    ddg_detail = ""
    if mock:
        ddg_detail = "mock mode — search not probed"
    elif not ddg_installed:
        ddg_detail = "ddgs package not installed"
    elif not net_ok:
        ddg_detail = "DNS preflight failed (try again — DNS may be slow)"
    else:
        try:
            rows = await duckduckgo_search("vendor intelligence test", max_results=1)
            ddg_active = len(rows) > 0
            ddg_detail = f"{len(rows)} sample result(s)" if ddg_active else "0 results"
        except Exception as exc:
            ddg_detail = str(exc)[:200]
    out["duckduckgo"] = {
        "status": _status(ddg_active, skipped=mock),
        "package": ddg_pkg or "none",
        "installed": ddg_installed,
        "backends": configured_ddgs_backends(),
        "detail": ddg_detail,
    }

    searx_url = (settings.searxng_base_url or "").strip()
    if not searx_url:
        out["searxng"] = {"status": "skipped", "url": "", "detail": "no SEARXNG_BASE_URL"}
    else:
        try:
            ping_ok = await searxng_ping(searx_url)
            out["searxng"] = {
                "status": _status(ping_ok, skipped=mock),
                "url": searx_url,
                "detail": "reachable" if ping_ok else "not reachable",
            }
        except Exception as exc:
            out["searxng"] = {
                "status": "inactive",
                "url": searx_url,
                "detail": str(exc)[:200],
            }

    llm_configured = llm_ph.is_configured()
    if mock:
        llm_status = "skipped"
        llm_detail = "mock mode"
    elif not llm_configured:
        llm_status = "inactive"
        llm_detail = "no API key for LLM_PROVIDER"
    elif llm_responded_ok is True:
        llm_status = "active"
        llm_detail = "compiler returned valid JSON (or partial scope) this run"
    elif llm_responded_ok is False:
        llm_status = "inactive"
        llm_detail = "compiler failed or regex fallback used"
    else:
        llm_status = "configured"
        llm_detail = "key present; compile not run in this step"
    out["llm"] = {
        "status": llm_status,
        "provider": settings.llm_provider,
        "detail": llm_detail,
    }

    from vendor_intel.placeholders.wikidata import is_enabled as wikidata_is_enabled

    wikidata_enabled = settings.wikidata_enabled and wikidata_is_enabled()
    wikidata_active = False
    wikidata_detail = ""
    if not wikidata_enabled:
        wikidata_detail = "disabled"
    elif mock:
        wikidata_detail = "mock mode — not probed"
    else:
        try:
            from vendor_intel.placeholders.wikidata import lookup_parent_org

            parent, _ = await lookup_parent_org("Microsoft")
            wikidata_active = parent is not None
            wikidata_detail = f"sample parent lookup: {parent or 'none'}"
        except Exception as exc:
            wikidata_detail = str(exc)[:200]
    out["wikidata"] = {
        "status": _status(wikidata_active, skipped=mock or not wikidata_enabled),
        "enabled": wikidata_enabled,
        "detail": wikidata_detail,
    }

    web_enabled = settings.web_fetch_enabled and SCRAPING_ENABLED
    web_active = False
    web_detail = ""
    if not web_enabled:
        web_detail = "WEB_FETCH_ENABLED=false"
    elif mock:
        web_detail = "mock mode — not probed"
    else:
        try:
            alive, final = await check_url_alive("https://example.com")
            web_active = alive
            web_detail = (
                f"ddgs.extract OK → {final}" if alive else "ddgs.extract / scrape fetch failed"
            )
        except Exception as exc:
            web_detail = str(exc)[:200]
    out["web_fetch"] = {
        "status": _status(web_active, skipped=mock or not web_enabled),
        "enabled": web_enabled,
        "detail": web_detail,
    }

    alerts_enabled = settings.google_alerts_enabled
    store = Path(settings.google_alerts_store_path)
    if not store.is_absolute():
        from vendor_intel.config import _project_root

        store = _project_root() / store
    if not alerts_enabled:
        out["google_alerts"] = {
            "status": "skipped",
            "enabled": False,
            "detail": "GOOGLE_ALERTS_ENABLED=false",
        }
    else:
        article_count = 0
        if store.is_file():
            try:
                import json

                data = json.loads(store.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    articles = data.get("articles")
                    article_count = len(articles) if isinstance(articles, list) else 0
                elif isinstance(data, list):
                    article_count = len(data)
                else:
                    article_count = 0
            except Exception:
                article_count = 0
        out["google_alerts"] = {
            "status": _status(store.is_file() and article_count > 0),
            "enabled": True,
            "store_path": str(store),
            "article_count": article_count,
            "detail": "store readable" if store.is_file() else "store missing — run alerts worker",
        }

    return out
