"""Shared async HTTP helpers for placeholder API calls."""
from __future__ import annotations

from vendor_intel.clients.http_proxy import httpx_async_client

DEFAULT_TIMEOUT = 30.0
USER_AGENT = "VendorIntelPipeline/0.1 (research; +https://github.com/local)"


async def post_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: dict | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    h = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        h.update(headers)
    async with httpx_async_client(timeout=timeout) as client:
        r = await client.post(url, headers=h, json=json_body or {})
        r.raise_for_status()
        return r.json()


async def get_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    h = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        h.update(headers)
    async with httpx_async_client(timeout=timeout) as client:
        r = await client.get(url, headers=h, params=params or {})
        r.raise_for_status()
        return r.json()


async def head_ok(url: str, timeout: float = 15.0) -> tuple[bool, str]:
    import httpx

    if not url.startswith("http"):
        url = f"https://{url}"
    try:
        async with httpx_async_client(timeout=timeout) as client:
            r = await client.head(url)
            if r.status_code >= 400:
                r = await client.get(url)
            final = str(r.url)
            return r.status_code < 400, final
    except httpx.HTTPError:
        return False, url
