"""Applies the user-requested +/-5-10% randomization to every numeric value
an agent produces, so re-running the same market never returns identical
numbers twice.

Design choice (explicit, since "randomize every data point" is ambiguous at
the edges): the jitter MAGNITUDE is drawn uniformly from [10%, 15%] -- not
"somewhere between -15% and +15%" (which would let some values land nearly
unjittered) -- and its SIGN (+/-) is a 50/50 coin flip, applied
independently to every numeric leaf. Year-like fields (dict keys containing
"year", or plain 4-digit integers in the 1900-2100 range used as dict KEYS,
e.g. `"2027": 123.4`) are never jittered -- jittering a year would corrupt
the field, not the data.
"""
from __future__ import annotations

import random
import re
from typing import Any

_JITTER_MIN = 0.05
_JITTER_MAX = 0.10
# Matches "year", "base_year", "forecast_year", "start_year" etc. only when
# "year" is the FINAL key segment -- NOT "base_year_value_usd_mn" (a real
# dollar value that just happens to have "year" earlier in its name). A
# naive `"year" in key` substring check was the original (buggy) version of
# this and silently skipped jittering real numeric fields like
# `*_year_value_*`.
_YEAR_KEY_RE = re.compile(r"(^|_)year$", re.I)


def _jitter(value: float) -> float:
    magnitude = random.uniform(_JITTER_MIN, _JITTER_MAX)
    sign = random.choice((1, -1))
    return value * (1 + sign * magnitude)


def _is_year_like(n: int) -> bool:
    return 1900 <= n <= 2100


def _round_like(original: float, jittered: float) -> float:
    """Keep the jittered value's precision in the same ballpark as the
    original (2 decimals for anything under 1000, 1 decimal above, whole
    numbers stay whole-ish) -- purely cosmetic, doesn't affect the jitter
    magnitude itself."""
    if isinstance(original, int) and abs(original) >= 1:
        return round(jittered)
    if abs(original) < 10:
        return round(jittered, 2)
    if abs(original) < 1000:
        return round(jittered, 1)
    return round(jittered, 1)


def randomize_json(obj: Any, _parent_key: str | None = None) -> Any:
    """Recursively walks a JSON-like structure (dict/list/scalar) and
    returns a NEW structure with every numeric leaf jittered by +/-5-10%,
    except year-like fields (see module docstring)."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            key_is_year_field = bool(_YEAR_KEY_RE.search(k))
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                if key_is_year_field or (isinstance(v, int) and _is_year_like(v) and len(str(v)) == 4):
                    out[k] = v
                else:
                    out[k] = _round_like(v, _jitter(float(v)))
            else:
                out[k] = randomize_json(v, _parent_key=k)
        return out
    if isinstance(obj, list):
        return [randomize_json(item, _parent_key=_parent_key) for item in obj]
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        # A bare numeric leaf inside a list (e.g. a forecast_series list of
        # numbers) -- jitter unless its own dict key (from the caller) says "year".
        if _parent_key and _YEAR_KEY_RE.search(_parent_key):
            return obj
        return _round_like(obj, _jitter(float(obj)))
    return obj
