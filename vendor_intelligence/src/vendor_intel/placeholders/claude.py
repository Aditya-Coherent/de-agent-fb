"""Anthropic Claude — delegates to llm router when LLM_PROVIDER=anthropic."""
from __future__ import annotations

from typing import Any

from vendor_intel.placeholders import llm as llm_router

ANTHROPIC_API_KEY = llm_router.ANTHROPIC_API_KEY
DEFAULT_COMPILER_MODEL = llm_router.DEFAULT_COMPILER_MODEL
DEFAULT_BATCH_MODEL = "claude-3-5-haiku-latest"
DEFAULT_CLASSIFIER_MODEL = llm_router.DEFAULT_COMPILER_MODEL


def _is_configured() -> bool:
    return llm_router.is_configured() and llm_router.LLM_PROVIDER == "anthropic"


def claude_complete(
    system: str,
    user: str,
    *,
    model: str | None = None,
    max_tokens: int = 8192,
) -> str:
    return llm_router.llm_complete(system, user, model=model, max_tokens=max_tokens)


def claude_complete_json(system: str, user: str, *, model: str | None = None) -> Any:
    return llm_router.llm_complete_json(system, user, model=model)
