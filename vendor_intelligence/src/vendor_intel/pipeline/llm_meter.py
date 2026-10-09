"""Lightweight LLM call counter for pipeline cost estimates."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LlmMeter:
    calls: int = 0
    phase1_compile: int = 0
    market_understanding: int = 0
    classify: int = 0
    export_profile: int = 0
    smart_crawl_chunks: int = 0
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0

    def add_classify(self, tokens_in: int = 800, tokens_out: int = 150) -> None:
        self.calls += 1
        self.classify += 1
        self.estimated_input_tokens += tokens_in
        self.estimated_output_tokens += tokens_out

    def add_profile(self, tokens_in: int = 1200, tokens_out: int = 400) -> None:
        self.calls += 1
        self.export_profile += 1
        self.estimated_input_tokens += tokens_in
        self.estimated_output_tokens += tokens_out

    def add_phase1(self, tokens_in: int = 4000, tokens_out: int = 2000) -> None:
        self.calls += 1
        self.phase1_compile += 1
        self.estimated_input_tokens += tokens_in
        self.estimated_output_tokens += tokens_out

    def add_market_understanding(self, tokens_in: int = 3500, tokens_out: int = 2500) -> None:
        self.calls += 1
        self.market_understanding += 1
        self.estimated_input_tokens += tokens_in
        self.estimated_output_tokens += tokens_out

    def summary(self, *, provider: str = "opencode", model: str = "") -> dict:
        # OpenCode free tier
        cost_usd = 0.0
        if provider not in ("opencode", "mock") and self.estimated_input_tokens:
            cost_usd = (self.estimated_input_tokens * 0.15 + self.estimated_output_tokens * 0.6) / 1_000_000
        return {
            "llm_calls_total": self.calls,
            "phase1_compile_calls": self.phase1_compile,
            "market_understanding_calls": self.market_understanding,
            "classify_calls": self.classify,
            "export_profile_calls": self.export_profile,
            "smart_crawl_chunk_calls": self.smart_crawl_chunks,
            "estimated_input_tokens": self.estimated_input_tokens,
            "estimated_output_tokens": self.estimated_output_tokens,
            "estimated_cost_usd": round(cost_usd, 4),
            "provider": provider,
            "model": model,
            "note": "Free on OpenCode Zen; paid if using OpenAI/Anthropic keys.",
        }


_global_meter: LlmMeter | None = None


def get_meter() -> LlmMeter:
    global _global_meter
    if _global_meter is None:
        _global_meter = LlmMeter()
    return _global_meter


def reset_meter() -> LlmMeter:
    global _global_meter
    _global_meter = LlmMeter()
    return _global_meter
