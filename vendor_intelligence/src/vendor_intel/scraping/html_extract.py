"""Extract plain text from Selenium-rendered pages."""
from __future__ import annotations

import re

import trafilatura


def body_fallback(html: str) -> str:
    cleaned = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def extract_text_from_html(
    html: str,
    *,
    visible_text: str = "",
    max_chars: int = 4000,
) -> str:
    """Prefer Chrome-visible text, then trafilatura, then HTML strip fallback."""
    vis = (visible_text or "").strip()
    if len(vis) >= 80:
        return vis[:max_chars]

    if not html.strip():
        return vis[:max_chars] if vis else ""

    text = trafilatura.extract(html, include_comments=False, favor_precision=False) or ""
    if len(text.strip()) < 80:
        text = body_fallback(html)
    if vis and vis not in text:
        combined = f"{vis}\n\n{text}".strip()
        return combined[:max_chars]
    return (text or vis)[:max_chars]
