"""Best-effort secret redaction applied to any `llm.prompt`/`llm.response`/
`source.url` text before it is written to the audit log.

Deliberately simple, regex-based -- never a full secret-scanner. Anything
that looks like a bearer token / `sk-...`-style API key / an
`api_key=...`-shaped assignment is replaced with `[REDACTED]`.
"""
from __future__ import annotations

import re

_PATTERNS = [
    re.compile(r"Bearer\s+[A-Za-z0-9._-]+", re.IGNORECASE),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"api[_-]?key[\"'\s:=]+[A-Za-z0-9._-]{16,}", re.IGNORECASE),
]


def redact_secrets(text: str) -> str:
    """Returns `text` with any bearer-token/API-key-looking substring
    replaced by `[REDACTED]`. Non-string input is returned unchanged;
    empty/falsy input is returned as-is."""
    if not isinstance(text, str) or not text:
        return text
    out = text
    for pattern in _PATTERNS:
        out = pattern.sub("[REDACTED]", out)
    return out
