"""One number format for the whole report -- tables, chart labels and text
all call these, so the same value is always printed the same way."""
from __future__ import annotations

import re

PRECISION_RULE = ("Values of $1B and above are shown to two decimals ($5.15B); $10M-$999M to three "
                  "significant figures ($218M, $88.2M); below $10M to two decimals ($6.27M); CAGRs and shares to "
                  "one decimal (12.3%); percentage-point gaps to one decimal (0.3 pp).")


def usd(mn: float) -> str:
    """USD-million value -> '$120.31B' / '$4.10B' / '$412M' / '$45.3M' / '$4.12M'.
    The ONLY money format in the report (tables, charts and text alike)."""
    a = abs(mn)
    if a >= 1000:
        return f"${mn / 1000:,.2f}B"
    if a >= 100:
        return f"${mn:,.0f}M"
    if a >= 10:
        return f"${mn:,.1f}M"
    return f"${mn:,.2f}M"


def usd_in(mn: float, unit: str | None = None) -> str:
    """Kept for callers; one fact = one format, so this is always usd()."""
    return usd(mn)


def unit_for(values: list[float]) -> str:
    return "auto"


def incr(mn: float, unit: str | None = None) -> str:
    return ("+" if mn >= 0 else "-") + usd(abs(mn))


_MEA_NAME = re.compile(r"(Middle East\s*(?:&|and)\s*Africa)(?!\s*\(MEA\))")


def mea(text: str) -> str:
    """'Middle East & Africa' always followed by '(MEA)' (user decision 2026-10-20), never twice."""
    return _MEA_NAME.sub(r"\1 (MEA)", text) if text else text


def pct(x: float, d: int = 1) -> str:
    return f"{x:.{d}f}%"


def signed(x: float) -> str:
    """A percentage-point gap with its sign and no unit ('-1.2', '+0.6')."""
    return f"{x:+.1f}".replace("-0.0", "0.0").replace("+0.0", "0.0")


def pp(x: float) -> str:
    return f"{signed(x)} pp"


def share(part: float, whole: float) -> str:
    """A share, always computed and printed the same way (one decimal)."""
    return pct(part / whole * 100) if whole else "0.0%"


def year_label(y: int, estimate_year: int = 2026) -> str:
    """'2025A' (the base year is actual), '2026E' (estimate), '2027F' onward (forecast)."""
    return f"{y}A" if y < estimate_year else f"{y}E" if y == estimate_year else f"{y}F"


def lower_name(name: str) -> str:
    """A market name in running text: words lowercased, acronyms and single letters kept
    ('Generative AI in Agriculture' -> 'generative AI in agriculture', 'Vitamin C' -> 'vitamin C')."""
    return " ".join(w if (len(w) > 1 and w.isupper()) or (len(w) == 1 and w.isupper() and i) else w.lower()
                    for i, w in enumerate(str(name).split()))
