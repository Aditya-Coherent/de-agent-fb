"""LLM router — Anthropic, Gemini, Groq, or OpenCode Zen via env LLM_PROVIDER."""
from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "YOUR_ANTHROPIC_API_KEY_HERE")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_CHAT_URL = "https://api.deepseek.com/chat/completions"
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-chat-v3-0324")
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "YOUR_GEMINI_API_KEY_HERE")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "YOUR_GROQ_API_KEY_HERE")
OPENCODE_API_KEY = os.getenv("OPENCODE_API_KEY", "")
OPENCODE_MODEL = os.getenv("OPENCODE_MODEL", "deepseek-v4-flash-free")
OPENCODE_FALLBACK_MODELS = [
    m.strip()
    for m in os.getenv(
        "OPENCODE_FALLBACK_MODELS",
        "deepseek-v4-flash-free,big-pickle",
    ).split(",")
    if m.strip()
]
LLM_FALLBACK_TO_OPENCODE = os.getenv("LLM_FALLBACK_TO_OPENCODE", "true").strip().lower() in (
    "true",
    "1",
    "yes",
)


def _opencode_skip_response_format(model: str) -> bool:
    """Free Zen models often reject json_object / json_schema."""
    m = (model or "").lower()
    return any(x in m for x in ("deepseek", "nemotron", "free", "big-pickle"))

DEFAULT_COMPILER_MODEL = "claude-sonnet-4-6"
DEFAULT_GEMINI_MODEL = "gemini-2.0-flash"
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
OPENCODE_ZEN_CHAT_URL = "https://opencode.ai/zen/v1/chat/completions"

_TIMEOUT = 120.0


def _key_ok(key: str) -> bool:
    return bool(key) and not key.startswith("YOUR_")


def _opencode_key() -> str:
    if _key_ok(OPENCODE_API_KEY):
        return OPENCODE_API_KEY
    if LLM_PROVIDER == "opencode" and _key_ok(ANTHROPIC_API_KEY):
        return ANTHROPIC_API_KEY
    return ""


def _anthropic_quota_exceeded(status: int, detail: str) -> bool:
    d = (detail or "").lower()
    if status not in (400, 402, 403, 429):
        return False
    return any(
        x in d
        for x in (
            "usage limit",
            "rate limit",
            "quota",
            "billing",
            "credit balance",
            "insufficient",
        )
    )


def _opencode_fallback_model(requested: str | None) -> str:
    """When falling back from Anthropic, ignore claude-* model ids."""
    req = (requested or "").strip()
    if req and "claude" not in req.lower():
        return req
    return OPENCODE_MODEL


def is_configured() -> bool:
    if LLM_PROVIDER == "deepseek":
        return _key_ok(DEEPSEEK_API_KEY)
    if LLM_PROVIDER == "openrouter":
        return _key_ok(OPENROUTER_API_KEY)
    if LLM_PROVIDER == "gemini":
        return _key_ok(GEMINI_API_KEY)
    if LLM_PROVIDER == "groq":
        return _key_ok(GROQ_API_KEY)
    if LLM_PROVIDER == "opencode":
        return bool(_opencode_key())
    if LLM_PROVIDER in ("mock", "none"):
        return False
    return _key_ok(ANTHROPIC_API_KEY)


def _repair_json_text(text: str) -> str:
    """Best-effort fixes for common LLM JSON mistakes (no extra dependencies)."""
    s = (text or "").strip()
    if not s:
        return s
    s = s.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
    # Trailing commas before } or ]
    s = re.sub(r",\s*([}\]])", r"\1", s)
    # Missing comma between objects in arrays: "}\n  {" -> "},\n  {"
    s = re.sub(r"\}\s*\{", "},{", s)
    # Missing comma between array elements that are strings: '"\n  "' -> '",\n  "'
    s = re.sub(r'"\s*\n\s*"', '",\n"', s)
    return s


def _extract_json(text: str) -> Any:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty LLM response")
    candidates: list[str] = [text]
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        candidates.insert(0, fence.group(1).strip())
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])

    last_err: json.JSONDecodeError | None = None
    for raw in candidates:
        for attempt_text in (raw, _repair_json_text(raw)):
            if not attempt_text:
                continue
            try:
                return json.loads(attempt_text)
            except json.JSONDecodeError as e:
                last_err = e
                continue
    if last_err:
        raise last_err
    raise ValueError("no JSON object in LLM response")


def salvage_compiler_payload(raw: str) -> dict[str, Any]:
    """Extract scope / prompts from broken JSON using brace matching."""
    out: dict[str, Any] = {}
    text = (raw or "").strip()
    if not text:
        return out

    def _extract_object(key: str) -> dict | list | None:
        pat = re.compile(rf'"{re.escape(key)}"\s*:\s*(\{{|\[)', re.I)
        m = pat.search(text)
        if not m:
            return None
        open_ch = m.group(1)
        close_ch = "}" if open_ch == "{" else "]"
        start = m.start(1)
        depth = 0
        for i in range(start, len(text)):
            c = text[i]
            if c == open_ch:
                depth += 1
            elif c == close_ch:
                depth -= 1
                if depth == 0:
                    chunk = text[start : i + 1]
                    for candidate in (chunk, _repair_json_text(chunk)):
                        try:
                            return json.loads(candidate)
                        except json.JSONDecodeError:
                            continue
                    return None
        return None

    scope = _extract_object("scope")
    if isinstance(scope, dict):
        out["scope"] = scope
    for key in ("funnel_prompts", "discovery_prompts", "prompts"):
        val = _extract_object(key)
        if isinstance(val, list) and val:
            out[key] = val
            break
    return out


def _stub_complete(_system: str, _user: str) -> str:
    return json.dumps({"status": "placeholder"})


def _post_json(url: str, headers: dict, body: dict) -> dict:
    import time

    import httpx
    from vendor_intel.clients.http_proxy import httpx_client

    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            with httpx_client(timeout=_TIMEOUT, skip_pool=True) as client:
                r = client.post(url, headers=headers, json=body)
                r.raise_for_status()
                return r.json()
        except (httpx.ConnectError, httpx.NetworkError, OSError) as exc:
            last_exc = exc
            err = str(exc).lower()
            if attempt < 2 and ("getaddrinfo" in err or getattr(exc, "errno", None) == 11001):
                time.sleep(2.0 * (attempt + 1))
                continue
            raise
    if last_exc:
        raise last_exc
    raise RuntimeError("post_json failed")


def _anthropic_complete(system: str, user: str, model: str, max_tokens: int) -> str:
    data = _post_json(
        "https://api.anthropic.com/v1/messages",
        {
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        },
    )
    return "".join(p.get("text", "") for p in data.get("content") or [] if p.get("type") == "text")


def _opencode_message_text(message: dict[str, Any]) -> str:
    """deepseek-v4-flash-free often fills reasoning_content and leaves content empty."""
    content = str(message.get("content") or "").strip()
    if content:
        return content
    reasoning = str(message.get("reasoning_content") or "").strip()
    if not reasoning:
        return ""
    # Some responses embed the final JSON only at the end of the reasoning trace.
    if "{" in reasoning:
        start = reasoning.rfind("{")
        end = reasoning.rfind("}")
        if end > start:
            return reasoning[start : end + 1].strip()
    return ""


def _opencode_effective_max_tokens(max_tokens: int) -> int:
    """Reasoning models burn completion tokens on thinking before the visible answer."""
    return max(max_tokens, 512)


def _gemini_complete(system: str, user: str, model: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    data = _post_json(
        url,
        {"x-goog-api-key": GEMINI_API_KEY, "content-type": "application/json"},
        {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.2},
        },
    )
    candidates = data.get("candidates") or []
    if not candidates:
        return ""
    return "".join(p.get("text", "") for p in candidates[0].get("content", {}).get("parts") or [])


def _opencode_complete(system: str, user: str, model: str, max_tokens: int) -> str:
    """OpenCode Zen — OpenAI-compatible chat API (free models e.g. deepseek-v4-flash-free)."""
    api_key = _opencode_key()
    headers = {"Authorization": f"Bearer {api_key}", "content-type": "application/json"}
    token_budget = _opencode_effective_max_tokens(max_tokens)

    def _call(body_tokens: int, extra: dict[str, Any]) -> tuple[str, str]:
        base_body: dict[str, Any] = {
            "model": model,
            "max_tokens": body_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.1,
            **extra,
        }
        data = _post_json(OPENCODE_ZEN_CHAT_URL, headers, base_body)
        choices = data.get("choices") or []
        if not choices:
            return "", ""
        choice = choices[0]
        msg = choice.get("message") or {}
        return _opencode_message_text(msg), str(choice.get("finish_reason") or "")

    if _opencode_skip_response_format(model):
        extras: list[dict[str, Any]] = [{}, {"response_format": {"type": "json_object"}}]
    else:
        extras = [{"response_format": {"type": "json_object"}}, {}]
    for extra in extras:
        try:
            text, finish = _call(token_budget, extra)
            if not text and finish == "length" and token_budget < 16384:
                text, _ = _call(min(token_budget * 2, 16384), extra)
            if text:
                return text
        except httpx.HTTPStatusError as e:
            if extra and e.response.status_code in (400, 422):
                continue
            if e.response.status_code >= 500:
                return json.dumps(
                    {
                        "error": f"http_{e.response.status_code}: {e.response.text[:200]}",
                        "status": "llm_failed",
                    }
                )
            raise
    return json.dumps(
        {
            "error": "empty_response: model used token budget on reasoning; retry or raise max_tokens",
            "status": "llm_failed",
        }
    )


def _deepseek_complete(system: str, user: str, model: str, max_tokens: int) -> str:
    """DeepSeek official API (platform.deepseek.com) — OpenAI-compatible chat completions."""
    body = {
        "model": model or DEEPSEEK_MODEL,
        "max_tokens": max(256, min(int(max_tokens), 8192)),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
    }
    # deepseek-chat supports JSON mode; deepseek-reasoner does not. Only request JSON mode when the
    # prompt actually asks for JSON — DeepSeek rejects json_object if the word "json" is absent, and
    # plain-text complete() calls (e.g. drafting a brief) must NOT be forced into JSON.
    if "reasoner" not in (model or DEEPSEEK_MODEL).lower() and "json" in f"{system}\n{user}".lower():
        body["response_format"] = {"type": "json_object"}
    data = _post_json(
        DEEPSEEK_CHAT_URL,
        {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "content-type": "application/json"},
        body,
    )
    choices = data.get("choices") or []
    return str(choices[0].get("message", {}).get("content", "")) if choices else ""


def _openrouter_complete(system: str, user: str, model: str, max_tokens: int) -> str:
    """OpenRouter — OpenAI-compatible chat completions. The model is FORCED to the configured
    DeepSeek model; a Claude/Sonnet id is never sent through (per requirement)."""
    mdl = (model or OPENROUTER_MODEL).strip()
    if not mdl or "claude" in mdl.lower():
        mdl = OPENROUTER_MODEL  # never Sonnet — always DeepSeek
    body = {
        "model": mdl,
        "max_tokens": max(256, min(int(max_tokens), 8192)),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        # Route to the fastest available backend (avoids slow/free hosts like DeepInfra).
        "provider": {"sort": "throughput"},
    }
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "content-type": "application/json",
        "X-Title": "VendorIntel",
    }
    low = mdl.lower()
    extras = [{}] if ("r1" in low or "reasoner" in low) else [
        {"response_format": {"type": "json_object"}}, {}
    ]
    for extra in extras:
        try:
            data = _post_json(OPENROUTER_CHAT_URL, headers, {**body, **extra})
        except httpx.HTTPStatusError as e:
            if extra and e.response.status_code in (400, 422):
                continue  # provider rejected json mode — retry plain
            raise
        choices = data.get("choices") or []
        if choices:
            msg = choices[0].get("message", {}) or {}
            return str(msg.get("content") or msg.get("reasoning_content") or "")
        return ""
    return ""


def _groq_complete(system: str, user: str, model: str, max_tokens: int) -> str:
    data = _post_json(
        "https://api.groq.com/openai/v1/chat/completions",
        {"Authorization": f"Bearer {GROQ_API_KEY}", "content-type": "application/json"},
        {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        },
    )
    choices = data.get("choices") or []
    return str(choices[0].get("message", {}).get("content", "")) if choices else ""


import threading as _threading

_CALL_LOCK = _threading.Lock()
_API_CALLS = 0
_CACHE_HITS = 0


def llm_api_call_count() -> int:
    """Real provider API requests made by this process since the last reset (cache hits excluded)."""
    return _API_CALLS


def llm_cache_hit_count() -> int:
    return _CACHE_HITS


def reset_llm_call_counts() -> None:
    global _API_CALLS, _CACHE_HITS
    with _CALL_LOCK:
        _API_CALLS = 0
        _CACHE_HITS = 0


def _bump(kind: str) -> None:
    global _API_CALLS, _CACHE_HITS
    with _CALL_LOCK:
        if kind == "api":
            _API_CALLS += 1
        else:
            _CACHE_HITS += 1


_LLM_CACHE_ENABLED = os.getenv("LLM_CACHE", "1").strip().lower() not in ("0", "false", "no", "off")


def _llm_cache_path(provider: str, model: str, system: str, user: str, max_tokens: int):
    import hashlib
    from pathlib import Path

    from vendor_intel.config import _project_root

    h = hashlib.sha256()
    h.update(f"{provider}\x00{model}\x00{max_tokens}\x00{system}\x00{user}".encode("utf-8", "ignore"))
    d = _project_root() / "output" / ".llm_cache"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        return None
    return d / f"{h.hexdigest()}.txt"


def llm_complete(
    system: str,
    user: str,
    *,
    model: str | None = None,
    max_tokens: int = 8192,
) -> str:
    """Caching wrapper: identical (provider, model, system, user, max_tokens) returns the saved
    response instead of re-calling the API. Output-neutral — only successful responses are cached
    (errors/stubs never are), so a repeat is byte-identical to what the API would have returned.
    Disable with LLM_CACHE=0."""
    if not is_configured():
        return _stub_complete(system, user)
    cache_file = None
    if _LLM_CACHE_ENABLED:
        cache_file = _llm_cache_path(LLM_PROVIDER, str(model or ""), system, user, max_tokens)
        if cache_file is not None:
            try:
                if cache_file.exists():
                    _bump("cache")
                    return cache_file.read_text(encoding="utf-8")
            except Exception:
                pass
    _bump("api")
    result = _llm_complete_raw(system, user, model=model, max_tokens=max_tokens)
    if (
        cache_file is not None
        and isinstance(result, str)
        and result.strip()
        and '"status": "llm_failed"' not in result
        and '"status": "placeholder"' not in result
    ):
        try:
            tmp = cache_file.with_suffix(".tmp")
            tmp.write_text(result, encoding="utf-8")
            tmp.replace(cache_file)
        except Exception:
            pass
    return result


def _llm_complete_raw(
    system: str,
    user: str,
    *,
    model: str | None = None,
    max_tokens: int = 8192,
) -> str:
    if not is_configured():
        return _stub_complete(system, user)
    try:
        if LLM_PROVIDER == "deepseek":
            return _deepseek_complete(system, user, model or DEEPSEEK_MODEL, max_tokens)
        if LLM_PROVIDER == "openrouter":
            return _openrouter_complete(system, user, model or OPENROUTER_MODEL, max_tokens)
        if LLM_PROVIDER == "gemini":
            return _gemini_complete(system, user, model or DEFAULT_GEMINI_MODEL)
        if LLM_PROVIDER == "groq":
            return _groq_complete(system, user, model or DEFAULT_GROQ_MODEL, max_tokens)
        if LLM_PROVIDER == "opencode":
            return _opencode_complete(system, user, model or OPENCODE_MODEL, max_tokens)
        return _anthropic_complete(system, user, model or DEFAULT_COMPILER_MODEL, max_tokens)
    except httpx.HTTPStatusError as e:
        detail = ""
        try:
            detail = e.response.text[:500]
        except Exception:
            pass
        if (
            LLM_PROVIDER == "anthropic"
            and LLM_FALLBACK_TO_OPENCODE
            and _opencode_key()
            and _anthropic_quota_exceeded(e.response.status_code, detail)
        ):
            fb = _opencode_fallback_model(model)
            print(
                f"  [llm] Anthropic limit hit — falling back to OpenCode ({fb})",
                flush=True,
            )
            return _opencode_complete(system, user, fb, max_tokens)
        return json.dumps(
            {
                "error": f"http_{e.response.status_code}: {detail or str(e)}",
                "status": "llm_failed",
            }
        )
    except httpx.HTTPError as e:
        return json.dumps({"error": str(e), "status": "llm_failed"})


def opencode_model_chain(primary: str | None = None) -> list[str]:
    """Ordered OpenCode models to try (primary first, then fallbacks)."""
    chain: list[str] = []
    if primary:
        chain.append(primary)
    for m in OPENCODE_FALLBACK_MODELS:
        if m not in chain:
            chain.append(m)
    if not chain:
        chain = [OPENCODE_MODEL]
    return chain


def opencode_models_for_attempt(attempt: int, primary: str | None = None) -> str:
    """Rotate OpenCode models when the primary returns 500 or empty."""
    chain = opencode_model_chain(primary)
    return chain[attempt % len(chain)]


def llm_complete_json(
    system: str,
    user: str,
    *,
    model: str | None = None,
    max_tokens: int = 4096,
) -> Any:
    raw = llm_complete(
        system + "\nRespond with valid JSON only. No markdown fences.",
        user,
        model=model,
        max_tokens=max_tokens,
    )
    if not raw or not str(raw).strip():
        return {"error": "empty_response", "status": "llm_failed"}
    raw_str = str(raw)
    try:
        parsed = _extract_json(raw_str)
        if isinstance(parsed, dict) and parsed.get("status") == "placeholder":
            return {"error": "placeholder", "status": "llm_failed"}
        return parsed
    except (ValueError, json.JSONDecodeError) as e:
        salvaged = salvage_compiler_payload(raw_str)
        if salvaged.get("scope"):
            salvaged["status"] = "llm_partial"
            salvaged["_salvaged"] = True
            return salvaged
        return {
            "error": f"json_parse_failed: {e}",
            "status": "llm_failed",
            "raw_preview": raw_str[:800],
            "_raw_text": raw_str,
        }
