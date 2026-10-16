"""Numbers are attributed to the entity they describe (false blockers in the 2026-10-16 runs)."""
from __future__ import annotations

from AiAgentsFleet.cmi_report import factcheck as f

NAMES = ["Middle East & Africa", "Middle East", "Africa"]


def _ents(sentence: str) -> dict[str, list[str]]:
    return {tok: ent for tok, _key, ent, _kind, _year, _cl in f.mentions(sentence, NAMES)}


def test_the_global_rate_after_a_region_is_global():
    # shrimp: "MEA cagr stated as 9.5% and 12.0%" -- 12.0% is the global rate
    e = _ents("Middle East & Africa is the smallest region at $40.3M in 2026E, forecast to reach $75.9M by 2033F "
              "at a 9.5% CAGR, 2.5 percentage points below the global 12.0% rate.")
    assert "Middle East & Africa" in e["9.5%"] and e["12.0%"] == ["global"]


def test_a_later_clause_keeps_the_sentence_subject():
    # GenAI: "global value 2033 stated as $1.94B and $175M" -- $175M is MEA's, after ", but grows to"
    e = _ents("The Middle East & Africa is the smallest region at $29.1M in 2026E, a CMI estimate, but grows to "
              "$175M by 2033F at a 29.2% CAGR, 1.1 percentage points above the global rate.")
    assert e["$175M"] == ["Middle East & Africa"] and e["29.2%"] == ["Middle East & Africa"]
