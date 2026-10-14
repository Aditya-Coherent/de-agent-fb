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
    # Runtime optimization (2026-08-26, Demand-Usage sizing only) -- bounded
    # worker count for parallel, independent I/O-bound evidence acquisition
    # (see `demand_usage/agents.py`). Desk Research does not read this field
    # at all, so this is purely additive/backward-compatible; defaulted so
    # every pre-existing `GenConfig(...)` construction (tests, callers) keeps
    # working unchanged.
    evidence_workers: int = 8
    # Desk Research: fetch the pages behind the market-size search results so
    # cited figures can be read from the page text, not only the snippet.
    fetch_pages: bool = False
    # Cited-figure evidence-quality steps (cited_values.py), each switchable so
    # one that hurts held-out accuracy can be turned off on its own.
    title_subject: bool = True    # page title / URL slug names the market when the sentence doesn't
    size_check: bool = True       # units x price range; reject figures >3x outside
    source_tiers: bool = True     # anchor on research-report pages / the CMI DB
    weak_consensus: bool = False  # disputed values -> nearest the size-range middle. OFF: worse on held-out (2026-10-09 eval)

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
            evidence_workers=max(1, int(os.environ.get("EVIDENCE_WORKERS", os.environ.get("DEAGENT_EVIDENCE_WORKERS", "8")))),
            fetch_pages=os.environ.get("DEAGENT_FETCH_PAGES", "1") == "1",
            title_subject=os.environ.get("DEAGENT_TITLE_SUBJECT", "1") == "1",
            size_check=os.environ.get("DEAGENT_SIZE_CHECK", "1") == "1",
            source_tiers=os.environ.get("DEAGENT_SOURCE_TIERS", "1") == "1",
            weak_consensus=os.environ.get("DEAGENT_WEAK_CONSENSUS", "0") == "1",
        )

    @property
    def is_online(self) -> bool:
        return bool(self.api_key)
