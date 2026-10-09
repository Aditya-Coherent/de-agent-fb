"""LLM client must use OpenCode model when LLM_PROVIDER=opencode."""
from __future__ import annotations

from vendor_intel.clients.claude import ClaudeClient
from vendor_intel.config import Settings


def test_opencode_uses_opencode_model_not_anthropic_compiler():
    settings = Settings.model_validate(
        {
            "llm_provider": "opencode",
            "opencode_model": "deepseek-v4-flash-free",
            "compiler_model": "claude-sonnet-4-20250514",
            "use_mock_data": False,
        }
    )
    client = ClaudeClient(settings)
    assert client._resolve_model() == "deepseek-v4-flash-free"
