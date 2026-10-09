"""Unified page fetch result (ddgs.extract or optional Selenium)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PageFetchResult:
    url: str
    final_url: str
    alive: bool
    text: str = ""
    html: str = ""
    title: str = ""
    source: str = "ddgs"
    error: str = ""

    @property
    def visible_text(self) -> str:
        """Alias used by profile/corporate scrape paths."""
        return self.text
