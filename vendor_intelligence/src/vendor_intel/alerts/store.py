"""JSON store for Google Alerts articles (backend)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from vendor_intel.alerts.models import AlertArticle
from vendor_intel.config import _project_root


def default_store_path() -> Path:
    return _project_root() / "data" / "alerts" / "articles.json"


class AlertStore:
    def __init__(self, path: Path | None = None):
        self.path = path or default_store_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> list[AlertArticle]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        items = raw if isinstance(raw, list) else raw.get("articles", [])
        return [AlertArticle.model_validate(a) for a in items if isinstance(a, dict)]

    def save(self, articles: list[AlertArticle]) -> None:
        payload = [a.model_dump() for a in articles]
        self.path.write_text(
            json.dumps({"articles": payload, "updated_at": datetime.now(timezone.utc).isoformat()}, indent=2),
            encoding="utf-8",
        )

    def upsert(self, new_articles: list[AlertArticle]) -> list[AlertArticle]:
        existing = {a.url: a for a in self.load()}
        for a in new_articles:
            existing[a.url] = a
        merged = list(existing.values())
        self.save(merged)
        return merged

    def articles_for_company(self, company_name: str, limit: int = 10) -> list[AlertArticle]:
        low = company_name.lower()
        hits = [
            a
            for a in self.load()
            if low in a.title.lower() or low in a.snippet.lower()
        ]
        return hits[:limit]
