"""Number ledger: the single source of truth for every number a run outputs
(`src/newinfo/system.md` §4, §6; format in `src/newinfo/documentation.md` Part A).

Every number gets one entry:

    number_id            {RUN}-{STRATEGY}-{GEO}-{KIND}-{SEQ}, e.g. R017-2A-ESP-QTY-0042
    name, value, unit    full precision; rounding happens only at display
    geo, year            ISO3 / region code / WLD
    label                verified_observed | source_found_unverified | derived |
                         estimated_from_facts | assumption
    weakest_input_label  least reliable label among all inputs, recursively
    formula, input_ids   for derived numbers
    source               for observed numbers: url, publisher, tier, quote
    module, pass_id, note

Derived numbers are COMPUTED here (`mul`, `total`, `scale`) from ledger
inputs -- never copied from an LLM -- and the builder below cross-checks them
against the sizing engine's own figures (`mismatches`). `documentation.md`
is generated from the ledger.
"""
from __future__ import annotations

import csv
import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

LABELS = ("verified_observed", "source_found_unverified", "derived", "estimated_from_facts", "assumption")
# Reliability order for `weakest_input_label` (documentation.md A.3); `derived`
# is not in it -- a derived number is only as good as its weakest input.
RELIABILITY = ("verified_observed", "source_found_unverified", "estimated_from_facts", "assumption")
KINDS = ("QTY", "PRC", "VAL", "SHR", "FAC", "PAR", "FX")

REGION_CODES = {
    "North America": "NAM", "Latin America": "LAM", "Europe": "EUR", "Asia Pacific": "APAC",
    "Middle East & Africa": "MEA", "Rest of World": "ROW",
}
_ISO3 = {
    "united states": "USA", "usa": "USA", "us": "USA", "canada": "CAN", "mexico": "MEX", "brazil": "BRA",
    "argentina": "ARG", "chile": "CHL", "colombia": "COL", "peru": "PER", "venezuela": "VEN",
    "united kingdom": "GBR", "uk": "GBR", "germany": "DEU", "france": "FRA", "italy": "ITA", "spain": "ESP",
    "portugal": "PRT", "netherlands": "NLD", "belgium": "BEL", "switzerland": "CHE", "austria": "AUT",
    "ireland": "IRL", "sweden": "SWE", "norway": "NOR", "denmark": "DNK", "finland": "FIN", "poland": "POL",
    "czech republic": "CZE", "czechia": "CZE", "hungary": "HUN", "romania": "ROU", "bulgaria": "BGR",
    "greece": "GRC", "croatia": "HRV", "serbia": "SRB", "albania": "ALB", "ukraine": "UKR", "russia": "RUS",
    "turkey": "TUR", "turkiye": "TUR", "china": "CHN", "japan": "JPN", "india": "IND", "south korea": "KOR",
    "korea": "KOR", "taiwan": "TWN", "hong kong": "HKG", "singapore": "SGP", "malaysia": "MYS",
    "thailand": "THA", "indonesia": "IDN", "philippines": "PHL", "vietnam": "VNM", "viet nam": "VNM",
    "australia": "AUS", "new zealand": "NZL", "pakistan": "PAK", "bangladesh": "BGD", "sri lanka": "LKA",
    "saudi arabia": "SAU", "united arab emirates": "ARE", "uae": "ARE", "qatar": "QAT", "kuwait": "KWT",
    "israel": "ISR", "iran": "IRN", "iraq": "IRQ", "jordan": "JOR", "lebanon": "LBN", "syria": "SYR",
    "egypt": "EGY", "south africa": "ZAF", "nigeria": "NGA", "kenya": "KEN", "morocco": "MAR",
    "algeria": "DZA", "tunisia": "TUN", "libya": "LBY", "ethiopia": "ETH", "palestine": "PSE",
}
_REST_OF_WORLD = re.compile(r"^rest of (the )?world$", re.IGNORECASE)


def geo_code(name: str) -> str:
    key = str(name or "").strip().lower()
    if not key or key == "global":
        return "WLD"
    if _REST_OF_WORLD.match(key):
        return "ROW"
    if name in REGION_CODES:
        return REGION_CODES[name]
    if key in _ISO3:
        return _ISO3[key]
    if re.fullmatch(r"[a-z]{3}", key):
        return key.upper()
    return re.sub(r"[^A-Z]", "", str(name).upper())[:3] or "UNK"


def weakest(labels: Iterable[str]) -> str:
    worst = 0
    for lab in labels:
        worst = max(worst, RELIABILITY.index(lab) if lab in RELIABILITY else RELIABILITY.index("assumption"))
    return RELIABILITY[worst]


@dataclass
class Number:
    number_id: str
    name: str
    value: float
    unit: str
    geo: str
    year: int | None
    label: str
    weakest_input_label: str
    formula: str = ""
    input_ids: list[str] = field(default_factory=list)
    source: dict[str, Any] | None = None
    module: str = ""
    pass_id: int | None = None
    note: str = ""


class LedgerError(ValueError):
    pass


class Ledger:
    def __init__(self, run_id: str, strategy: str, pass_id: int | None = None) -> None:
        self.run_id, self.strategy, self.pass_id = run_id, strategy, pass_id
        self.rows: list[Number] = []
        self._by_id: dict[str, Number] = {}
        self.mismatches: list[dict[str, Any]] = []

    # ---- writing -------------------------------------------------------- #
    def _next_id(self, geo: str, kind: str) -> str:
        if kind not in KINDS:
            raise LedgerError(f"unknown kind {kind!r}")
        return f"{self.run_id}-{self.strategy}-{geo}-{kind}-{len(self.rows) + 1:04d}"

    def _put(self, n: Number) -> Number:
        if n.label not in LABELS:
            raise LedgerError(f"{n.number_id}: missing/unknown label {n.label!r}")
        if n.value is None or (isinstance(n.value, float) and math.isnan(n.value)):
            raise LedgerError(f"{n.number_id}: value is NaN/None")
        if n.label == "assumption" and not n.note:
            raise LedgerError(f"{n.number_id}: an assumption needs a written reason (note)")
        self.rows.append(n)
        self._by_id[n.number_id] = n
        return n

    def fact(self, name: str, value: float, unit: str, geo: str, kind: str, label: str, *, year: int | None = None,
             source: dict[str, Any] | None = None, module: str = "", note: str = "",
             weakest_input_label: str | None = None) -> Number:
        """A leaf number: observed, estimated, or an assumption (with a reason)."""
        if label == "derived" and weakest_input_label is None:
            raise LedgerError(f"{name}: a derived leaf must state its weakest input label")
        g = geo_code(geo)
        return self._put(Number(self._next_id(g, kind), name, float(value), unit, g, year, label,
                                weakest_input_label or weakest([label]), source=source, module=module,
                                pass_id=self.pass_id, note=note))

    def _derived(self, name: str, value: float, unit: str, geo: str, kind: str, formula: str,
                 inputs: list[Number], *, year: int | None = None, module: str = "calc", note: str = "") -> Number:
        g = geo_code(geo)
        return self._put(Number(self._next_id(g, kind), name, float(value), unit, g, year, "derived",
                                weakest(i.weakest_input_label for i in inputs), formula,
                                [i.number_id for i in inputs], module=module, pass_id=self.pass_id, note=note))

    def derive(self, name: str, value: float, unit: str, geo: str, kind: str, formula: str, inputs: list[Number], *,
               year: int | None = None, module: str = "calc", note: str = "") -> Number:
        """A derived number from any formula over ledger inputs (ratios, conversions,
        an agent's arithmetic re-evaluated by code); the caller computes the value."""
        if not inputs:
            raise LedgerError(f"{name}: a derived number needs its inputs")
        return self._derived(name, value, unit, geo, kind, formula, inputs, year=year, module=module, note=note)

    def mul(self, name: str, a: Number, b: Number, unit: str, geo: str, kind: str = "VAL", *,
            factor: float = 1.0, year: int | None = None, note: str = "") -> Number:
        formula = f"{a.number_id} × {b.number_id}" + (f" × {factor:g}" if factor != 1.0 else "")
        return self._derived(name, a.value * b.value * factor, unit, geo, kind, formula, [a, b], year=year, note=note)

    def total(self, name: str, parts: list[Number], unit: str, geo: str, kind: str = "VAL", *,
              year: int | None = None, note: str = "") -> Number:
        if not parts:
            raise LedgerError(f"{name}: nothing to sum")
        return self._derived(name, math.fsum(p.value for p in parts), unit, geo, kind,
                             "Σ(" + ", ".join(p.number_id for p in parts) + ")", parts, year=year, note=note)

    def scale(self, name: str, a: Number, f: Number, *, note: str = "") -> Number:
        """An adjustment (anchor, publisher): the factor is its own ledger row."""
        return self._derived(name, a.value * f.value, a.unit, a.geo, a.number_id.split("-")[3],
                             f"{a.number_id} × {f.number_id}", [a, f], year=a.year, note=note)

    def check(self, number: Number, engine_value: float, what: str, rel_tol: float = 1e-4) -> None:
        """Record a mismatch between a ledger-derived number and the engine's figure."""
        if not math.isclose(number.value, engine_value, rel_tol=rel_tol, abs_tol=1e-6):
            self.mismatches.append({"number_id": number.number_id, "what": what,
                                    "ledger": number.value, "engine": engine_value})

    def get(self, number_id: str) -> Number:
        return self._by_id[number_id]

    # ---- reading -------------------------------------------------------- #
    def find(self, *, kind: str | None = None, geo: str | None = None, name_prefix: str | None = None) -> list[Number]:
        return [n for n in self.rows if (kind is None or n.number_id.split("-")[3] == kind)
                and (geo is None or n.geo == geo) and (name_prefix is None or n.name.startswith(name_prefix))]

    def label_counts(self) -> dict[str, int]:
        out = {lab: 0 for lab in LABELS}
        for n in self.rows:
            out[n.label] += 1
        return out

    def to_records(self) -> list[dict[str, Any]]:
        return [asdict(n) for n in self.rows]

    # ---- persistence ---------------------------------------------------- #
    def write(self, out_dir: Path) -> dict[str, Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        recs = self.to_records()
        paths = {"json": out_dir / "ledger.json", "csv": out_dir / "ledger.csv"}
        paths["json"].write_text(json.dumps({"run_id": self.run_id, "strategy": self.strategy,
                                             "mismatches": self.mismatches, "numbers": recs},
                                            indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        flat = [{**r, "input_ids": " ".join(r["input_ids"]),
                 "source": json.dumps(r["source"], ensure_ascii=False) if r["source"] else ""} for r in recs]
        with paths["csv"].open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(flat[0]) if flat else ["number_id"])
            writer.writeheader()
            writer.writerows(flat)
        try:
            import pandas as pd

            paths["parquet"] = out_dir / "ledger.parquet"
            pd.DataFrame(flat).to_parquet(paths["parquet"], index=False)
        except Exception:  # noqa: BLE001 -- parquet is optional (needs pyarrow)
            paths.pop("parquet", None)
        return paths


# --------------------------------------------------------------------------- #
# display rules (documentation.md A.4)
# --------------------------------------------------------------------------- #
def sig(value: float, figs: int = 3) -> float:
    if value == 0:
        return 0.0
    return round(value, -int(math.floor(math.log10(abs(value)))) + (figs - 1))


def fmt_usd(value_usd: float, figs: int = 3) -> str:
    v = sig(value_usd, figs)
    for div, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            return f"${v / div:.{figs}g}{suffix}"
    return f"${v:.{figs}g}"


def fmt_pct(share: float, decimals: int = 1) -> str:
    return f"{share * 100:.{decimals}f}%"


def fmt_tonnes(t: float) -> str:
    return f"{round(t):,} t" if abs(t) < 100_000 else f"{sig(t, 3):,.0f} t"


def fmt_inr(value_inr: float, fx_inr_per_usd: float) -> str:
    crore = value_inr / 1e7
    text = f"₹{crore:,.1f} crore" if crore >= 1 else f"₹{value_inr / 1e5:,.1f} lakh"
    return f"{text} (US${sig(value_inr / fx_inr_per_usd, 3):,.0f} at ₹{fx_inr_per_usd:g}/US$)"


def fmt_value(n: Number) -> str:
    unit = n.unit.lower()
    if unit == "usd":
        return fmt_usd(n.value)
    if unit in ("t", "tonnes", "tonne"):
        return fmt_tonnes(n.value)
    if unit in ("share", "dimensionless", "ratio"):
        return fmt_pct(n.value) if n.value <= 1.5 else f"{n.value:.3g}"
    return f"{sig(n.value, 4):,.6g} {n.unit}"


# --------------------------------------------------------------------------- #
# documentation.md generator
# --------------------------------------------------------------------------- #
def write_documentation(ledger: Ledger, path: Path, header: dict[str, Any]) -> Path:
    counts = ledger.label_counts()
    L = [f"# Number documentation — {header.get('market', '')} ({ledger.strategy})", "",
         f"Run `{ledger.run_id}` · pass {ledger.pass_id} · base year {header.get('base_year')} · "
         f"config `{header.get('config_hash', '')}` · generated from the ledger ({len(ledger.rows)} numbers).", "",
         "Labels: " + ", ".join(f"{k} {v}" for k, v in counts.items() if v), ""]
    if header.get("headline"):
        L += [f"**Headline:** {header['headline']}", ""]
    if ledger.mismatches:
        L += ["**Ledger vs engine mismatches:**", ""] + [
            f"- {m['number_id']} ({m['what']}): ledger {m['ledger']:,.4f} vs engine {m['engine']:,.4f}"
            for m in ledger.mismatches] + [""]
    for note in header.get("notes") or []:
        L.append(f"- {note}")
    if header.get("notes"):
        L.append("")
    titles = {"QTY": "Quantities", "PRC": "Prices", "VAL": "Values", "SHR": "Shares and ratios",
              "FAC": "Adjustment factors", "PAR": "Model parameters", "FX": "FX"}
    for kind in KINDS:
        rows = ledger.find(kind=kind)
        if not rows:
            continue
        L += [f"## {titles[kind]}", "", "| ID | Name | Value | Label | Weakest input | Formula / source | Note |",
              "|---|---|---|---|---|---|---|"]
        for n in rows:
            src = n.formula
            if n.source:
                s = n.source
                src = (f"[{s.get('publisher') or 'source'}]({s['url']})" if s.get("url") else (s.get("publisher") or ""))
                if s.get("tier"):
                    src += f" (tier {s['tier']})"
                if s.get("quote"):
                    src += f": “{str(s['quote'])[:160]}”"
            L.append(f"| {n.number_id} | {n.name} | {fmt_value(n)} | {n.label} | {n.weakest_input_label} | "
                     f"{src.replace('|', '/')} | {n.note[:200].replace('|', '/')} |")
        L.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L), encoding="utf-8")
    return path
