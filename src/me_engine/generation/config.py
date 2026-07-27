"""Configuration for the synthetic generator (reads .env / environment)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True, slots=True)
class GenConfig:
    api_key: str
    base_url: str
    model: str
    timeout: float
    neon_url: str | None
    enable_web: bool
    max_loop_iters: int
    min_loop_iters: int
    reward_threshold: float
    per_region_shares: bool

    @classmethod
    def load(cls, project_root: Path | str = ".") -> "GenConfig":
        _load_dotenv(Path(project_root) / ".env")
        key = (
            os.environ.get("DEEPSEEK_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or os.environ.get("OPENROUTER_API_KEY")
            or ""
        )
        base = os.environ.get("DEAGENT_LLM_BASE_URL") or os.environ.get(
            "ME_LLM_BASE_URL", "https://api.deepseek.com"
        )
        model = os.environ.get("DEAGENT_MODEL") or os.environ.get("ME_CURVE_MODEL", "deepseek-chat")
        return cls(
            api_key=key,
            base_url=base,
            model=model,
            timeout=float(os.environ.get("ME_LLM_TIMEOUT", "90")),
            neon_url=os.environ.get("NEON_DATABASE_URL"),
            enable_web=os.environ.get("ME_ENABLE_WEB_EVIDENCE", "0") == "1",
            max_loop_iters=int(os.environ.get("DEAGENT_MAX_LOOP_ITERS", "4")),
            min_loop_iters=int(os.environ.get("DEAGENT_MIN_LOOP_ITERS", "2")),
            reward_threshold=float(os.environ.get("DEAGENT_REWARD_THRESHOLD", "0.9")),
            per_region_shares=os.environ.get("DEAGENT_PER_REGION_SHARES", "1") == "1",
        )

    @property
    def is_online(self) -> bool:
        return bool(self.api_key)
