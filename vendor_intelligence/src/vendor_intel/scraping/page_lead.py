"""Build profile/corporate lead text from ddgs.extract markdown or HTML."""
from __future__ import annotations

import re

import trafilatura

from vendor_intel.scraping.html_extract import body_fallback

_HEADING_RE = re.compile(
    r"<h([1-6])[^>]*>(.*?)</h\1>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_MD_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)


def _strip_tags(html_fragment: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub("", html_fragment)).strip()


def lead_from_markdown(
    markdown: str,
    *,
    max_lines: int = 15,
    max_chars: int = 1200,
) -> str:
    headings = [m.group(1).strip()[:200] for m in _MD_HEADING_RE.finditer(markdown)]
    headings = [h for h in headings if h][:8]
    lines = [ln.strip() for ln in markdown.splitlines() if ln.strip() and not ln.startswith("#")][
        :max_lines
    ]
    parts: list[str] = []
    if headings:
        parts.append("HEADINGS: " + " | ".join(headings))
    if lines:
        parts.append("LEAD:\n" + "\n".join(lines))
    return "\n".join(parts)[:max_chars]


def extract_headings_and_lead(
    html: str,
    *,
    max_lines: int = 15,
    max_chars: int = 1200,
) -> str:
    headings: list[str] = []
    for _level, raw in _HEADING_RE.findall(html):
        text = _strip_tags(raw)
        if text and text not in headings:
            headings.append(text[:200])
        if len(headings) >= 8:
            break

    body_text = trafilatura.extract(html, include_comments=False) or ""
    if not body_text.strip():
        body_text = body_fallback(html)
    lines = [ln.strip() for ln in body_text.splitlines() if ln.strip()][:max_lines]

    parts: list[str] = []
    if headings:
        parts.append("HEADINGS: " + " | ".join(headings))
    if lines:
        parts.append("LEAD:\n" + "\n".join(lines))
    return "\n".join(parts)[:max_chars]


def _lead_from_fetch_result(
    result_text: str,
    result_html: str,
    *,
    max_lines: int,
    max_chars: int,
) -> str:
    body = (result_text or "").strip()
    if body and not body.lstrip().startswith("<"):
        return lead_from_markdown(body, max_lines=max_lines, max_chars=max_chars)
    html = (result_html or body or "").strip()
    if html:
        return extract_headings_and_lead(html, max_lines=max_lines, max_chars=max_chars)
    return ""


async def fetch_page_lead(
    url: str,
    *,
    max_lines: int = 25,
    max_chars: int = 2800,
) -> tuple[bool, str, str]:
    if not url.startswith("http"):
        url = f"https://{url}"
    from vendor_intel.scraping.fetch import fetch_page

    result = await fetch_page(url)
    if not result.alive:
        return False, result.final_url or url, ""
    lead = _lead_from_fetch_result(
        result.text, result.html, max_lines=max_lines, max_chars=max_chars
    )
    if not lead:
        return False, result.final_url or url, ""
    return True, result.final_url, lead
