"""Settings shim for crawler/smart_crawl.py (reads .env; does not modify smart_crawl)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        root = Path(__file__).resolve().parents[1]
        env_path = root / ".env"
        if env_path.is_file():
            load_dotenv(env_path, override=False)
    except ImportError:
        pass


@dataclass(frozen=True)
class CrawlerSettings:
    openai_api_key: str
    model_extract: str


def sync_opencode_to_openai_env() -> None:
    """Use one API key: OPENCODE_* → OPENAI_* for smart_crawl (OpenAI-compatible Zen API)."""
    _load_dotenv()
    provider = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    if provider == "deepseek" and (os.getenv("DEEPSEEK_API_KEY") or "").strip():
        # DeepSeek is OpenAI-compatible: point the OpenAI client at DeepSeek with the DeepSeek
        # key. (Previously the OpenCode key was copied into OPENAI_API_KEY with no base URL, so
        # every smart_crawl LLM step hit api.openai.com and failed with 401.)
        if not (os.getenv("OPENAI_API_KEY") or "").strip():
            os.environ["OPENAI_API_KEY"] = os.environ["DEEPSEEK_API_KEY"].strip()
        if not (os.getenv("OPENAI_BASE_URL") or "").strip():
            os.environ["OPENAI_BASE_URL"] = "https://api.deepseek.com"
        if not (os.getenv("OPENAI_MODEL") or "").strip():
            os.environ["OPENAI_MODEL"] = (os.getenv("DEEPSEEK_MODEL") or "deepseek-chat").strip()
        return
    if not (os.getenv("OPENAI_API_KEY") or "").strip():
        oc = (os.getenv("OPENCODE_API_KEY") or "").strip()
        if oc:
            os.environ["OPENAI_API_KEY"] = oc
    if not (os.getenv("OPENAI_BASE_URL") or "").strip():
        base = (os.getenv("OPENCODE_BASE_URL") or "").strip()
        if not base and (os.getenv("LLM_PROVIDER") or "").lower() == "opencode":
            base = "https://opencode.ai/zen/v1"
        if base:
            os.environ["OPENAI_BASE_URL"] = base.rstrip("/")
    if not (os.getenv("OPENAI_MODEL") or "").strip():
        om = (os.getenv("OPENCODE_MODEL") or os.getenv("MODEL_EXTRACT") or "").strip()
        if om:
            os.environ["OPENAI_MODEL"] = om


@lru_cache(maxsize=1)
def get_settings() -> CrawlerSettings:
    sync_opencode_to_openai_env()
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    model = (os.getenv("OPENAI_MODEL") or os.getenv("MODEL_EXTRACT") or "gpt-4o-mini").strip()
    return CrawlerSettings(openai_api_key=key, model_extract=model)
