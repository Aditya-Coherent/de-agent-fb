"""Evidence extraction + deterministic verification -- the choke point that
stops "the LLM says this is observed" from ever being trusted at face value.

The LLM's job is only to point at a specific web-search snippet and read a
number out of it (or a regional/global figure plus a stated derivation
basis). Whether that number actually counts as `observed`/`derived`/
`assumption` is then decided here, in code, by checking whether the claimed
value is actually traceable inside the snippet text it says it came from.
Mirrors the discipline already used in `supply/estimator.py`
(`_ground_company`: real data fetched first, LLM only derives from it, never
trusted to self-report groundedness) and `supply/evidence_estimator.py`
(deterministic regex extraction, no LLM in that path at all -- here an LLM
is needed because driver units vary market-to-market, so extraction is
LLM-assisted, but acceptance is not).
"""
from __future__ import annotations

import re
from typing import Any

from me_engine.generation.llm import GenLLM

_SCALE_WORDS = {
    "trillion": 1e12, "billion": 1e9, "bn": 1e9,
    "million": 1e6, "mn": 1e6, "thousand": 1e3, "k": 1e3,
}
_NUM_RE = re.compile(
    r"([-+]?\d[\d,]*\.?\d*)\s*(trillion|billion|bn|million|mn|thousand|k)?\b",
    re.IGNORECASE,
)
_REL_TOL = 0.03  # 3% -- snippet text is prose, not a structured field


def _numbers_in_text(text: str) -> list[float]:
    out: list[float] = []
    for m in _NUM_RE.finditer(text or ""):
        raw, scale = m.group(1), m.group(2)
        try:
            val = float(raw.replace(",", ""))
        except ValueError:
            continue
        if val == 0 and not scale:
            continue  # bare "0" matches are noise, not real figures
        if scale:
            val *= _SCALE_WORDS[scale.lower()]
        out.append(val)
    return out


def _is_close(a: float, b: float, rel_tol: float = _REL_TOL) -> bool:
    if a == 0 and b == 0:
        return True
    return abs(a - b) / max(abs(a), abs(b), 1e-9) <= rel_tol


def verify_and_tag(fact: dict[str, Any]) -> tuple[str, float]:
    """Deterministic, code-only. Returns (evidence_type, confidence)."""
    evidence_text = str(fact.get("evidence_text") or "")
    value = fact.get("value")
    if not evidence_text or value is None:
        return "assumption", 0.2

    numbers = _numbers_in_text(evidence_text)
    value_traceable = any(_is_close(float(value), n) for n in numbers)
    is_direct = bool(fact.get("is_direct_figure", True))

    if value_traceable and is_direct:
        return "observed", 0.9
    if fact.get("derivation_basis") or value_traceable:
        # either explicitly named as a derivation from a bigger figure, or the
        # figure IS present in the snippet but was declared non-direct (e.g. a
        # regional total the LLM says it then allocated) -- either way it is
        # traceable to real text, just not a direct per-target figure.
        return "derived", 0.6
    return "assumption", 0.3


def extract_structured_fact(
    llm: GenLLM, snippets: list[str], *, unit: str, context: str
) -> dict[str, Any] | None:
    """Ask the LLM to point at ONE snippet and read a figure out of it.
    Returns None if there was no evidence to search at all, or the LLM
    honestly found nothing -- callers must treat that as "no evidence",
    never fabricate a number to fill the gap themselves."""
    if not snippets:
        return None

    system = (
        "You are an evidence-extraction analyst. You will be given real web-search "
        f"snippets. Your ONLY job is to find, inside those snippets, an explicit "
        f"numeric figure for: {context} (target unit: {unit}). Do NOT estimate or "
        "invent a number -- if no snippet states one, say so honestly by returning "
        "found=false. If a snippet only gives a regional/global figure instead of "
        "the exact one needed, you may propose deriving from it, but set "
        "is_direct_figure=false and name the derivation basis explicitly."
    )
    user = (
        f"{context}\nTarget unit: {unit}\n\nSNIPPETS:\n"
        + "\n".join(f"[{i}] {s}" for i, s in enumerate(snippets))
        + "\n\n"
        'Return JSON: {"found": <true|false>, "value": <number or null>, '
        '"snippet_index": <int index of the snippet this came from, or null>, '
        '"year": <int or null>, "source": "<short source label from the snippet>", '
        '"citation": "<the exact phrase in the snippet stating the figure>", '
        '"is_direct_figure": <true if the snippet states exactly this figure; false '
        'if derived from a bigger regional/global number>, "derivation_basis": '
        '"<if is_direct_figure is false, the formula/basis used, e.g. \'population '
        'share of region\'; else empty>"}'
    )
    try:
        data = llm.complete_json(system, user)
    except Exception:  # noqa: BLE001
        return None
    if not data or not data.get("found"):
        return None
    try:
        value = float(data.get("value"))
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None

    idx = data.get("snippet_index")
    snippet_text = snippets[idx] if isinstance(idx, int) and 0 <= idx < len(snippets) else ""

    fact: dict[str, Any] = {
        "value": value,
        "unit": unit,
        "year": data.get("year"),
        "source": str(data.get("source") or "").strip(),
        "citation": str(data.get("citation") or "").strip(),
        "evidence_text": snippet_text,
        "is_direct_figure": bool(data.get("is_direct_figure", True)),
        "derivation_basis": str(data.get("derivation_basis") or "").strip(),
    }
    fact["evidence_type"], fact["confidence"] = verify_and_tag(fact)
    return fact


_PROVENANCE_RANK = {"observed": 2, "derived": 1, "assumption": 0}
_RANK_TO_PROVENANCE = {v: k for k, v in _PROVENANCE_RANK.items()}


def worse_of(*evidence_types: str) -> str:
    """Overall provenance for a value built from multiple facts (e.g. driver x
    intensity) is never stronger than its weakest input."""
    ranks = [_PROVENANCE_RANK.get(t, 0) for t in evidence_types if t]
    if not ranks:
        return "assumption"
    return _RANK_TO_PROVENANCE[min(ranks)]
