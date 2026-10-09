from __future__ import annotations

from pydantic import BaseModel, Field


class AlertArticle(BaseModel):
    title: str
    url: str
    snippet: str = ""
    published_at: str = ""
    alert_query: str = ""
    collected_at: str = ""
