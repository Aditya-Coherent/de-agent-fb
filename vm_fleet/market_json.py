"""One JSON per market: everything the whitepaper was built from, in one file.

    output/<slug>/<slug>.json = {
      "meta":       market, slug, sheet/order in the workbook, result (ready | draft | failed),
                    release-gate blockers and warnings, run minutes, files,
      "definition": the client's definition (workbook) and the definition the report used,
      "model":      the single numeric model -- every number the report prints (cmi_model.json),
      "text":       the final text of every section (cmi_prose.json slots, after the guards),
      "agents":     the desk-research answers (fleet + CMI agents), without the raw search snippets,
    }

    python vm_fleet/market_json.py AiAgentsFleet/output/<slug> [...]     # rebuild for existing outputs

Read the files with market_json.load(path): JSON object keys are strings, so year series come back as
{"2025": ...}; load() turns them into {2025: ...} as the report code expects. json_to_pdf.py rebuilds
the full PDF from one of these files.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SCHEMA_VERSION = 1


def _load(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except json.JSONDecodeError:
        return {}


def _strip_snippets(v):
    """Agent answers without the stored search snippets (kept: domains, counts, quality)."""
    if isinstance(v, dict):
        return {k: _strip_snippets(x) for k, x in v.items() if k not in ("snippets", "shown", "evidence_snippets")}
    if isinstance(v, list):
        return [_strip_snippets(x) for x in v]
    return v


def build(mdir: Path, meta: dict | None = None) -> Path:
    mdir = Path(mdir)
    slug = mdir.name
    fleet = _load(mdir / "whitepaper_fields.json")
    cmi = _load(mdir / "cmi_fields.json")
    model = _load(mdir / "cmi_model.json")
    prose = _load(mdir / "cmi_prose.json")
    audit = _load(mdir / "audit_report.json")
    status = _load(mdir / "vm_status.json")
    gate = audit.get("gate") or {}
    # what the final build received (run_cmi_report writes it): the model with every later addition
    # (regional segments), the final text, and the agent data the document was filled from
    inputs = _load(mdir / "report_inputs.json")
    if inputs:
        model, prose = inputs.get("model") or model, inputs.get("prose") or prose
    files = sorted(p.name for p in mdir.iterdir() if p.suffix in (".docx", ".pdf", ".csv"))
    out = {
        "schema_version": SCHEMA_VERSION,
        "meta": {**(meta or {}), "slug": slug,
                 "market": fleet.get("market_name") or (meta or {}).get("market"),
                 "result": status.get("result") or ("ready" if gate.get("ready") else "draft" if gate else "failed"),
                 "ready": bool(gate.get("ready")), "blockers": gate.get("blockers") or [],
                 "warnings": gate.get("warnings") or [], "minutes": status.get("minutes"),
                 "generated_at": fleet.get("generated_at"), "finished": status.get("finished"),
                 "files": files, "charts": sorted(p.name for p in (mdir / "charts").glob("*.png"))},
        "definition": {"client": fleet.get("definition_inclusion_exclusion"),
                       "used": inputs.get("definition") or model.get("definition") or fleet.get("market_definition")},
        "model": model,
        "text": prose.get("slots") or {},
        "agents": {"fleet": _strip_snippets(fleet.get("fields") or {}),
                   "cmi": _strip_snippets(cmi.get("fields") or {})},
    }
    if inputs:
        # the agent data exactly as the document used it (verified facts applied, regional extras, sources)
        out["report_agents"] = inputs.get("agents") or {}
        out["toc_pages"] = inputs.get("toc_pages") or {}
    path = mdir / f"{slug}.json"
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    return path


def _year_keys(v):
    """JSON object keys are strings: {"2025": ...} back to {2025: ...}, as the model uses them."""
    if isinstance(v, dict):
        return {(int(k) if isinstance(k, str) and re.fullmatch(r"(19|20)\d\d", k) else k): _year_keys(x)
                for k, x in v.items()}
    if isinstance(v, list):
        return [_year_keys(x) for x in v]
    return v


def load(path: Path) -> dict:
    """A market JSON as the report code expects it (year keys as numbers). Read the files with this:
    a plain json.load leaves "2025" as a string key, and the chart/table code compares years as numbers."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out = {**data, "model": _year_keys(data.get("model") or {}), "agents": _year_keys(data.get("agents") or {})}
    if "report_agents" in data:
        out["report_agents"] = _year_keys(data["report_agents"])
    if data.get("toc_pages"):
        out["toc_pages"] = {int(k): v for k, v in data["toc_pages"].items()}
    return out


if __name__ == "__main__":
    for d in sys.argv[1:]:
        print(build(Path(d)))
