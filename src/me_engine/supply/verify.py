"""Cheap company-reality check: does a claimed/guessed website resolve?

Not a strong guarantee (a defunct real company's site can fail; a wrong-but-
plausible guessed domain fails even for a real company) — but a fast, free
signal that catches the exact pattern seen in LLM-only discovery output:
invented placeholder names ("Avocado Oil Chile") with no real business
behind them, since neither the LLM nor anyone else can guess a working
domain for a company that doesn't exist. General outbound HTTP works fine
in this environment (only the DDG HTML endpoint used elsewhere is blocked;
verified separately) so this check is reliable to run.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import httpx

from .companies import Company

_TIMEOUT = 8.0
_MAX_WORKERS = 8


def _normalize_url(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    return value


def check_website(website: str) -> dict:
    url = _normalize_url(website)
    if not url:
        return {"reachable": False, "detail": "no website given"}
    try:
        r = httpx.head(url, timeout=_TIMEOUT, follow_redirects=True)
        if r.status_code >= 400:
            r = httpx.get(url, timeout=_TIMEOUT, follow_redirects=True)
        return {"reachable": r.status_code < 400, "detail": f"HTTP {r.status_code}"}
    except Exception as exc:  # noqa: BLE001 - any network failure means "not reachable"
        return {"reachable": False, "detail": type(exc).__name__}


def _verify_one(company: Company) -> Company:
    website = (
        company.facts.get("website")
        or company.facts.get("Website")
        or company.facts.get("likely_website")
    )
    if not website:
        note = "No website to check — verify manually"
    else:
        result = check_website(str(website))
        note = (
            f"Website reachable ({result['detail']})"
            if result["reachable"]
            else f"Website unreachable ({result['detail']}) — possibly generic/placeholder, verify manually"
        )
    return Company(name=company.name, facts={**company.facts, "verification": note})


def verify_companies(companies: list[Company]) -> list[Company]:
    """Checks each company's website concurrently; returns new Company
    objects with a `verification` fact added (facts are otherwise unchanged)."""
    if not companies:
        return []
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        return list(pool.map(_verify_one, companies))
