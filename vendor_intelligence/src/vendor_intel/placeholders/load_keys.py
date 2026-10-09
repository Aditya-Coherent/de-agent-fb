"""Load API keys from environment into placeholder modules."""
from __future__ import annotations

import os


def apply_env_overrides() -> None:
    from dotenv import load_dotenv

    from vendor_intel.config import _project_root

    load_dotenv(_project_root() / ".env")

    from vendor_intel.placeholders import llm as llm_mod
    from vendor_intel.placeholders import web_fetch as wf
    from vendor_intel.placeholders import wikidata as wd
    from vendor_intel.scraping import fetch as scrape_fetch
    from vendor_intel.scraping.selenium_browser import apply_selenium_env

    if v := os.getenv("ANTHROPIC_API_KEY"):
        llm_mod.ANTHROPIC_API_KEY = v
    if v := os.getenv("DEEPSEEK_API_KEY"):
        llm_mod.DEEPSEEK_API_KEY = v
    if v := os.getenv("DEEPSEEK_MODEL"):
        llm_mod.DEEPSEEK_MODEL = v
    if v := os.getenv("OPENROUTER_API_KEY"):
        llm_mod.OPENROUTER_API_KEY = v
    if v := os.getenv("OPENROUTER_MODEL"):
        llm_mod.OPENROUTER_MODEL = v
    if v := os.getenv("GEMINI_API_KEY"):
        llm_mod.GEMINI_API_KEY = v
    if v := os.getenv("GROQ_API_KEY"):
        llm_mod.GROQ_API_KEY = v
    if v := os.getenv("OPENCODE_API_KEY"):
        llm_mod.OPENCODE_API_KEY = v
    if v := os.getenv("OPENCODE_MODEL"):
        llm_mod.OPENCODE_MODEL = v
    if v := os.getenv("LLM_PROVIDER"):
        llm_mod.LLM_PROVIDER = v

    if os.getenv("WEB_FETCH_ENABLED", "").lower() in ("0", "false", "no"):
        scrape_fetch.SCRAPING_ENABLED = False
        wf.WEB_FETCH_ENABLED = False
    if os.getenv("WIKIDATA_ENABLED", "").lower() in ("0", "false", "no"):
        wd.WIKIDATA_ENABLED = False

    apply_selenium_env()
