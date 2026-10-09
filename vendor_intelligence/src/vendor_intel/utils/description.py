"""Build a short company description from scrape lead or discovery snippets."""
from __future__ import annotations

from vendor_intel.models import DiscoveryHit, Entity


def _clean(text: str, max_len: int = 500) -> str:
    t = " ".join((text or "").split())
    if len(t) <= max_len:
        return t
    return t[: max_len - 3].rstrip() + "..."


def description_from_lead(lead_text: str) -> str:
    if not lead_text:
        return ""
    if "LEAD:" in lead_text:
        part = lead_text.split("LEAD:", 1)[-1].strip()
        return _clean(part, 500)
    if lead_text.startswith("HEADINGS:"):
        return _clean(lead_text.replace("HEADINGS:", "", 1).strip(), 400)
    return _clean(lead_text, 500)


def build_company_description(
    entity: Entity,
    hits: list[DiscoveryHit],
    *,
    lead_text: str = "",
) -> str:
    if lead_text:
        desc = description_from_lead(lead_text)
        if desc:
            return desc
    if entity.scraped_text:
        desc = description_from_lead(entity.scraped_text)
        if desc:
            return desc
    snippets: list[str] = []
    name_low = entity.canonical_name.lower()
    for h in hits:
        if name_low in h.name_raw.lower() or h.name_raw.lower() in name_low:
            sn = (h.snippet or "").strip()
            if sn and sn not in snippets:
                snippets.append(sn)
        if len(snippets) >= 2:
            break
    if snippets:
        return _clean(" ".join(snippets), 500)
    return ""
