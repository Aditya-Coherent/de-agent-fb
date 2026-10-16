"""The shared search module picks Perplexity when a key is set and falls back to ddgs
(me_engine/generation/web_evidence.py). No network: httpx and ddgs are mocked."""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from me_engine.generation import web_evidence as w  # noqa: E402

PPLX = {"results": [
    {"title": "GCC Fertilizers and Soil Conditioners Law", "url": "https://laws.boe.gov.sa/x",
     "snippet": "Registering the fertilizers and soil conditioners that can be imported...", "date": None},
    {"title": "Empty", "url": "https://a.example/y", "snippet": ""},
]}


class _Resp:
    def __init__(self, code, data=None):
        self.status_code, self._data = code, data

    def json(self):
        return self._data


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(w, "_dotenv_loaded", True)          # never read the real .env in tests
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    monkeypatch.delenv("WEB_SEARCH_BACKEND", raising=False)
    monkeypatch.setattr(w.time, "sleep", lambda s: None)
    return monkeypatch


def _ddgs(monkeypatch, rows):
    mod = types.ModuleType("ddgs")
    mod.DDGS = lambda: types.SimpleNamespace(text=lambda q, max_results: rows)
    monkeypatch.setitem(sys.modules, "ddgs", mod)


def _httpx(monkeypatch, responses):
    calls = []
    mod = types.ModuleType("httpx")

    def post(url, json, timeout, headers):
        calls.append({"url": url, "json": json, "headers": headers})
        return responses[min(len(calls) - 1, len(responses) - 1)]
    mod.post = post
    monkeypatch.setitem(sys.modules, "httpx", mod)
    return calls


def test_backend_choice(env):
    assert w.backend() == "ddgs"
    env.setenv("PERPLEXITY_API_KEY", "pplx-test")
    assert w.backend() == "perplexity"
    env.setenv("WEB_SEARCH_BACKEND", "ddgs")                  # forced old behaviour
    assert w.backend() == "ddgs"


def test_perplexity_results_become_snippets_with_sources(env):
    env.setenv("PERPLEXITY_API_KEY", "pplx-test")
    calls = _httpx(env, [_Resp(200, PPLX)])
    out = w.search_snippets_with_sources("saudi fertiliser registration", 5)
    assert calls[0]["url"] == w.PERPLEXITY_URL and calls[0]["headers"]["Authorization"] == "Bearer pplx-test"
    assert calls[0]["json"]["max_results"] == 5 and calls[0]["json"]["max_tokens_per_page"] > 0
    assert out == [{"text": "GCC Fertilizers and Soil Conditioners Law: Registering the fertilizers and soil "
                            "conditioners that can be imported...", "url": "https://laws.boe.gov.sa/x",
                    "domain": "laws.boe.gov.sa", "title": "GCC Fertilizers and Soil Conditioners Law"}]
    assert w.search_snippets("q", 5)[0].startswith("GCC Fertilizers")


def test_rate_limit_is_retried_then_succeeds(env):
    env.setenv("PERPLEXITY_API_KEY", "pplx-test")
    calls = _httpx(env, [_Resp(429), _Resp(200, PPLX)])
    assert len(w.search_snippets_with_sources("q", 5)) == 1 and len(calls) == 2


def test_perplexity_failure_falls_back_to_ddgs(env):
    env.setenv("PERPLEXITY_API_KEY", "pplx-bad")
    _httpx(env, [_Resp(401)])                                  # bad key: no retry, ddgs instead
    _ddgs(env, [{"title": "T", "href": "https://www.d.example/p", "body": "a ddgs result body long enough"}])
    out = w.search_snippets_with_sources("q", 5)
    assert out and out[0]["domain"] == "d.example"


def test_without_a_key_ddgs_is_used(env):
    _ddgs(env, [{"title": "T", "href": "https://d.example/p", "body": "a ddgs result body long enough"}])
    _httpx(env, [_Resp(500)])
    assert w.search_snippets("q", 5) == ["T: a ddgs result body long enough"]
