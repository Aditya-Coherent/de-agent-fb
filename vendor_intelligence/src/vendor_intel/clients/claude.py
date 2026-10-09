from __future__ import annotations

from typing import Any

from vendor_intel.config import Settings
from vendor_intel.placeholders import llm as ph
from vendor_intel.placeholders.claude import DEFAULT_COMPILER_MODEL


class ClaudeClient:
    """LLM client (Anthropic / Gemini / Groq / OpenCode Zen via LLM_PROVIDER)."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._model = settings.compiler_model

    @property
    def available(self) -> bool:
        if self._settings.use_mock_data or self._settings.mock_mode:
            return False
        return ph.is_configured()

    def _resolve_model(self, model: str | None = None) -> str:
        """Route to provider-specific model (OpenCode/Gemini/Groq — not Anthropic compiler_model)."""
        if self._settings.llm_provider == "opencode":
            req = (model or "").strip()
            if req and "claude" not in req.lower():
                return req
            return self._settings.opencode_model
        if self._settings.llm_provider == "deepseek":
            req = (model or "").strip()
            if req and "claude" not in req.lower():
                return req
            return self._settings.deepseek_model
        if self._settings.llm_provider == "openrouter":
            # Force the configured DeepSeek model; never pass a Claude/Sonnet id through.
            req = (model or "").strip()
            if req and "claude" not in req.lower():
                return req
            return self._settings.openrouter_model
        if model:
            return model
        if self._settings.llm_provider == "gemini":
            return self._settings.gemini_model
        if self._settings.llm_provider == "groq":
            return self._settings.groq_model
        return self._model

    def complete(self, system: str, user: str, model: str | None = None, max_tokens: int = 8192) -> str:
        return ph.llm_complete(
            system, user, model=self._resolve_model(model), max_tokens=max_tokens
        )

    def complete_json(
        self,
        system: str,
        user: str,
        model: str | None = None,
        *,
        max_tokens: int = 4096,
    ) -> Any:
        return ph.llm_complete_json(
            system, user, model=self._resolve_model(model), max_tokens=max_tokens
        )

    # --- Non-blocking variants for use inside async code -------------------------------------
    # The provider calls are synchronous httpx requests (up to 120s). Calling them directly from
    # a coroutine blocks the whole event loop, so an asyncio.Semaphore(12) around them still runs
    # one call at a time. These run the call in a worker thread so the configured concurrency
    # is real.
    async def acomplete(
        self, system: str, user: str, model: str | None = None, max_tokens: int = 8192
    ) -> str:
        import asyncio

        return await asyncio.to_thread(self.complete, system, user, model, max_tokens)

    async def acomplete_json(
        self,
        system: str,
        user: str,
        model: str | None = None,
        *,
        max_tokens: int = 4096,
    ) -> Any:
        import asyncio

        return await asyncio.to_thread(
            lambda: self.complete_json(system, user, model, max_tokens=max_tokens)
        )
