"""Checks that the market_size agent's headline really comes from the evidence
it was given (PLAN-02, FINDINGS-2026-10-15 X4).

The agent returns `basis` (the quotes its number came from) and `how_derived`.
A headline is **sourced** when a basis quote is found in the snippets (same
normalised match as `run_data._verified_published`, over the whole quote), the
quote states the basis value and year, and the headline is that value -- or
that value carried from 2024/2026 to the base year at a CAGR stated in a
verified quote -- within +/-2%. Anything else is **unsourced**: the agent is
re-asked once (`check_with_reask`), and if it is still unsourced the report
labels the headline a "CMI estimate" with no published-comparison wording.

No numbers are blended, averaged or taken from another approach here.
"""
from __future__ import annotations

import re
from typing import Callable

from AiAgentsFleet.cmi_report.run_data import _norm, _publisher

_DERIVE_TOLERANCE = 0.02           # headline vs basis value (or its normalised value)
_MAX_YEAR_GAP = 2                  # 2023-2027 values may be carried to a 2025 base
_MIN_QUOTE_CHARS = 25              # as run_data._verified_published

_UNIT_MN = {"trillion": 1e6, "tn": 1e6, "billion": 1e3, "bn": 1e3, "b": 1e3,
            "million": 1.0, "mn": 1.0, "mio": 1.0, "m": 1.0}
_AMOUNT = re.compile(r"(\d+(?:\.\d+)?)\s*(trillion|tn|billion|bn|b|million|mn|mio|m)\b", re.I)
_NON_USD = re.compile(r"(?:€|£|¥|₹|\bEUR|\bGBP|\bJPY|\bINR|\bRs\.?|\bCNY|\bRMB)\s*$", re.I)
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def _plain(text: str) -> str:
    """Thousands separators removed ('4,300 million' -> '4300 million')."""
    return re.sub(r"(?<=\d),(?=\d{3}\b)", "", text or "")


def amounts_usd_mn(quote: str) -> list[float]:
    """Every USD amount stated in a quote, in USD million. Amounts in another
    currency are skipped (no FX conversion is assumed)."""
    q = _plain(quote)
    out = []
    for mm in _AMOUNT.finditer(q):
        if _NON_USD.search(q[max(0, mm.start() - 6):mm.start()]):
            continue
        out.append(float(mm.group(1)) * _UNIT_MN[mm.group(2).lower()])
    return out


def _pcts(quote: str) -> list[float]:
    return [float(x) for x in _PCT.findall(_plain(quote))]


def _close(a: float, b: float, rel: float) -> bool:
    return bool(b) and abs(a / b - 1) <= rel


def _find(quote: str, sourced: list[dict]) -> dict | None:
    q = _norm(quote)
    if len(q) < _MIN_QUOTE_CHARS:
        return None
    return next((s for s in sourced if q in _norm(s.get("text", ""))), None)


def verify_market_size(ms: dict, sourced_snippets: list[dict]) -> dict:
    """{"status": "sourced" | "unsourced", "notes": [...], "headline", "source",
    "cagr_sourced", "how_derived"}. `sourced_snippets` are the snippets the
    agent was shown ({"text", "url", "domain"})."""
    notes: list[str] = []
    try:
        head = float(ms.get("base_year_value_usd_mn") or 0)
        base_year = int(ms.get("base_year") or 2025)
        head_cagr = ms.get("cagr_pct")
        head_cagr = float(head_cagr) if head_cagr is not None else None
    except (TypeError, ValueError):
        head, base_year, head_cagr = 0.0, 2025, None
    out = {"status": "unsourced", "notes": notes,
           "headline": {"base_year": base_year, "value_usd_mn": head, "cagr_pct": head_cagr},
           "source": None, "cagr_sourced": False, "how_derived": ms.get("how_derived")}
    basis = [b for b in ms.get("basis") or [] if isinstance(b, dict)]
    if not head:
        notes.append("no headline value")
        return out
    if not basis:
        notes.append("no basis quote given")
        return out

    # 1. which basis entries are real: quote in the evidence, value and year stated in the quote
    verified = []
    for i, b in enumerate(basis):
        quote = str(b.get("quote") or "")
        src = _find(quote, sourced_snippets)
        try:
            val = float(b.get("value_usd_mn"))
            year = int(b.get("year"))
        except (TypeError, ValueError):
            notes.append(f"basis[{i}]: value_usd_mn/year missing")
            continue
        if not src:
            notes.append(f"basis[{i}]: quote not found in the evidence: '{quote[:100]}'")
            continue
        if not any(_close(a, val, 0.005) for a in amounts_usd_mn(quote)):
            notes.append(f"basis[{i}]: value {val:g} (USD mn) is not stated in its quote")
            continue
        if str(year) not in quote:
            notes.append(f"basis[{i}]: year {year} is not stated in its quote")
            continue
        cagr = b.get("cagr_pct")
        cagr_ok = cagr is not None and any(abs(p - float(cagr)) <= 0.05 for p in _pcts(quote))
        if cagr is not None and not cagr_ok:
            notes.append(f"basis[{i}]: CAGR {cagr}% is not stated in its quote")
        verified.append({"i": i, "quote": quote, "value_usd_mn": val, "year": year,
                         "cagr_pct": float(cagr) if cagr_ok else None, "src": src,
                         "publisher": b.get("publisher")})
    quoted_cagrs = [v["cagr_pct"] for v in verified if v["cagr_pct"] is not None]

    # 2. the headline must be one of them, or one carried to the base year at a quoted CAGR
    for v in verified:
        gap = base_year - v["year"]
        if gap == 0:
            expected, how = v["value_usd_mn"], "as quoted"
        elif abs(gap) <= _MAX_YEAR_GAP:
            rate = v["cagr_pct"] if v["cagr_pct"] is not None else (quoted_cagrs[0] if quoted_cagrs else None)
            if rate is None:
                notes.append(f"basis[{v['i']}]: {v['year']} value but no quoted CAGR to carry it to {base_year}")
                continue
            expected = v["value_usd_mn"] * (1 + rate / 100) ** gap
            how = f"{v['year']} value carried to {base_year} at the quoted {rate:g}% CAGR"
        else:
            notes.append(f"basis[{v['i']}]: year {v['year']} is too far from base year {base_year}")
            continue
        if not _close(head, expected, _DERIVE_TOLERANCE):
            notes.append(f"basis[{v['i']}]: headline {head:g} is not {expected:,.1f} ({how}) within "
                         f"+/-{_DERIVE_TOLERANCE:.0%}")
            continue
        dom = v["src"].get("domain", "")
        out["status"] = "sourced"
        out["source"] = {"quote": v["quote"], "value_usd_mn": v["value_usd_mn"], "year": v["year"],
                         "cagr_pct": v["cagr_pct"], "derivation": how, "url": v["src"].get("url"),
                         "domain": dom, "publisher": _publisher(v["publisher"] or "", dom)}
        break
    else:
        if verified and not notes:
            notes.append("no verified quote supports the headline")
    out["cagr_sourced"] = head_cagr is not None and any(abs(c - head_cagr) <= 0.1 for c in quoted_cagrs)
    if out["status"] == "sourced" and not out["cagr_sourced"]:
        notes.append("CAGR is not stated in a verified quote (CMI estimate)")
    return out


def reask_note(check: dict) -> str:
    """What to tell the agent when its headline failed the check."""
    return ("\n\nYOUR PREVIOUS ANSWER FAILED THE SOURCE CHECK:\n"
            + "\n".join(f"- {n}" for n in check["notes"])
            + "\nEvery basis.quote must be copied EXACTLY, character for character, from one WEB EVIDENCE line "
              "above and must itself state the value and its year. base_year_value_usd_mn must be that value, or "
              "a 2024/2026 value carried to the base year with a CAGR that a quote states. If no evidence line "
              "states a size for this market, return \"basis\": [] and your best estimate. Return the full JSON again.")


def check_with_reask(ms: dict, sourced: list[dict], ask: Callable[[str], dict],
                     log: list[str]) -> tuple[dict, dict]:
    """Verifies the answer; when unsourced, asks once more (`ask(note)` returns
    the agent's new JSON) and keeps the new answer if it has a headline.
    Returns (answer, check)."""
    check = verify_market_size(ms, sourced)
    if check["status"] == "sourced":
        check["reasked"] = False
    else:
        log.append("[market_size] headline not backed by the evidence (" + "; ".join(check["notes"])[:300]
                   + ") -- re-asking once")
        first = {"status": check["status"], "notes": check["notes"]}
        try:
            again = ask(reask_note(check))
        except Exception as exc:  # noqa: BLE001
            log.append(f"[market_size] re-ask failed: {exc}")
            again = None
        if isinstance(again, dict) and isinstance(again.get("base_year_value_usd_mn"), (int, float)):
            ms = again
            check = verify_market_size(ms, sourced)
        check["reasked"] = True
        check["first_attempt"] = first
    if check["status"] == "sourced":
        s = check["source"]
        log.append(f"[market_size] headline sourced: {s['publisher']} ({s['domain']}), {s['derivation']}")
    else:
        log.append("[market_size] headline UNSOURCED after re-ask -- the report labels it a CMI estimate")
    return ms, check
