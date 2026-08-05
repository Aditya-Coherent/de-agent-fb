"""Load a company/distributor list from whatever format the source agent hands us.

Supported inputs:
- .csv / .xlsx — the company-finder agent's report format: a header row
  (#, Company, Brand, Parent_or_Independent, Website, Functionality,
  Geography, Is_Relevant, Summary) with section-break rows in between
  ("=== Manufacturers of X (38) ===" in the .csv, "Manufacturers of X  (38)"
  as a lone-cell row in the .xlsx). Only the "Manufacturers" section is kept
  by default — raw-material suppliers sell into a different upstream
  market, and distributor revenue includes resale markup and often spans
  multiple manufacturers/categories, so neither maps cleanly onto this
  market's size (see the demand-vs-supply discussion this exists for).
- .json — a flat list of names, or a list of objects with at least a
  "name" key and any number of extra fact keys (country, known_revenue,
  ...) threaded through to the estimator as context.
- .txt  — one company name per line.
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

_SECTION_RE = re.compile(r"^=*\s*(.+?)\s*\(\d+\)\s*=*$")


@dataclass(frozen=True, slots=True)
class Company:
    name: str
    facts: dict = field(default_factory=dict)


def _section_name(cell: str) -> str | None:
    """If `cell` is a section-break row ('=== Manufacturers of X (38) ==='
    or the bare '(38)'-suffixed variant), return the section label."""
    m = _SECTION_RE.match(cell.strip())
    return m.group(1).strip() if m else None


def _rows_to_companies(rows: list[list[str]], sections: tuple[str, ...]) -> list[Company]:
    header_idx = next((i for i, r in enumerate(rows) if "Company" in r), None)
    if header_idx is None:
        # No recognizable header — treat every non-empty first cell as a name.
        return [Company(name=str(r[0]).strip()) for r in rows if r and str(r[0]).strip()]

    header = [str(h).strip() for h in rows[header_idx]]
    col = {h: i for i, h in enumerate(header) if h}
    name_col = col.get("Company")
    if name_col is None:
        return []

    skip_cols = {"#", "Company", "Is_Relevant", "Summary", "Website"}
    fact_cols = [c for c in header if c and c not in skip_cols and c != "Company"]
    wanted = tuple(s.lower() for s in sections)

    companies: list[Company] = []
    current_section: str | None = None
    for row in rows[header_idx + 1:]:
        cells = [str(c).strip() if c is not None else "" for c in row]
        non_empty = [c for c in cells if c]
        if len(non_empty) == 1:
            section = _section_name(non_empty[0]) or non_empty[0]
            current_section = section
            continue
        if len(cells) <= name_col or not cells[name_col]:
            continue
        if wanted and not any(w in (current_section or "").lower() for w in wanted):
            continue
        is_relevant_col = col.get("Is_Relevant")
        if is_relevant_col is not None and len(cells) > is_relevant_col:
            if cells[is_relevant_col] and cells[is_relevant_col].lower() not in ("yes", ""):
                continue
        facts = {c: cells[col[c]] for c in fact_cols if len(cells) > col[c] and cells[col[c]]}
        if current_section:
            facts["category"] = current_section
        companies.append(Company(name=cells[name_col], facts=facts))
    return companies


def _load_csv(path: Path, sections: tuple[str, ...]) -> list[Company]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    return _rows_to_companies(rows, sections)


def _load_xlsx(path: Path, sections: tuple[str, ...]) -> list[Company]:
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=True, read_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    return _rows_to_companies(rows, sections)


def load_companies(path: Path | str, sections: tuple[str, ...] = ("Manufacturer",)) -> list[Company]:
    """`sections` filters to section-break labels containing any of these
    (case-insensitive) substrings; pass () to keep everything. Only applies
    to the .csv/.xlsx company-finder report format — .json/.txt have no
    section concept."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Company list not found: {path}")

    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _load_csv(path, sections)
    if suffix == ".xlsx":
        return _load_xlsx(path, sections)

    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        companies: list[Company] = []
        for item in data:
            if isinstance(item, str):
                companies.append(Company(name=item.strip()))
            elif isinstance(item, dict):
                name = str(item.get("name") or item.get("company") or "").strip()
                if not name:
                    continue
                facts = {k: v for k, v in item.items() if k not in ("name", "company") and v}
                companies.append(Company(name=name, facts=facts))
        return companies

    # Plain text: one company per line.
    lines = path.read_text(encoding="utf-8").splitlines()
    return [Company(name=line.strip()) for line in lines if line.strip()]


def save_companies_csv(companies: list[Company], path: Path | str) -> Path:
    """Persist a discovered/loaded list to the same `my_companies/` CSV shape
    (no section markers — a flat "Manufacturers" block) so it can be re-loaded
    by `load_companies` and sits alongside company-finder-sourced files."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fact_keys: list[str] = []
    for c in companies:
        for k in c.facts:
            if k not in fact_keys:
                fact_keys.append(k)

    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["#", "Company", *fact_keys])
        writer.writerow([f"=== Manufacturers ({len(companies)}) ==="])
        for i, c in enumerate(companies, start=1):
            writer.writerow([i, c.name, *[c.facts.get(k, "") for k in fact_keys]])
    return path
