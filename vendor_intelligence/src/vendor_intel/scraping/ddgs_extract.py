"""Fetch and extract page content via ddgs.DDGS.extract()."""
from __future__ import annotations

import os

from vendor_intel.clients.ddgs_engines import run_with_ddgs
from vendor_intel.clients.duckduckgo import wait_before_ddg_https_request
from vendor_intel.clients.http_proxy import ensure_outbound_proxies
from vendor_intel.scraping.page_result import PageFetchResult


def _normalize_url(url: str) -> str:
    url = url.strip()
    if not url.startswith("http"):
        url = f"https://{url}"
    return url


def scrape_extract_fmt() -> str:
    """text_markdown | text_plain | text_rich | text | content"""
    fmt = (os.getenv("SCRAPE_EXTRACT_FMT") or "text_markdown").strip()
    if fmt in ("text_markdown", "text_plain", "text_rich", "text", "content"):
        return fmt
    return "text_markdown"


def fetch_page_via_ddgs_extract(url: str) -> PageFetchResult:
    """
    ddgs.extract(url, fmt=...) — uses same proxy pool as search.
    """
    url = _normalize_url(url)
    fmt = scrape_extract_fmt()
    ensure_outbound_proxies()
    wait_before_ddg_https_request()

    try:

        def _extract(ddgs: object) -> dict:
            if not hasattr(ddgs, "extract"):
                raise RuntimeError(
                    "ddgs.extract() not available — upgrade: pip install -U ddgs"
                )
            out = ddgs.extract(url, fmt=fmt)  # type: ignore[attr-defined]
            if not isinstance(out, dict):
                raise RuntimeError("ddgs.extract returned non-dict")
            return out

        out = run_with_ddgs(_extract, label="ddgs.extract")
    except RuntimeError as exc:
        return PageFetchResult(
            url=url,
            final_url=url,
            alive=False,
            error=str(exc),
        )
    except Exception as exc:
        return PageFetchResult(
            url=url,
            final_url=url,
            alive=False,
            error=f"{type(exc).__name__}: {exc}"[:400],
        )

    final = str(out.get("url") or url)
    raw = out.get("content", "")
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="replace")
    else:
        text = str(raw or "")

    html = text if fmt == "text" else ""
    alive = len(text.strip()) >= 80
    return PageFetchResult(
        url=url,
        final_url=final,
        alive=alive,
        text=text.strip(),
        html=html,
        source="ddgs",
    )
