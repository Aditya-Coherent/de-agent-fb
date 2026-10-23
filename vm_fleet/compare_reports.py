"""Are two CMI whitepapers built the same way? Compares their structure, not their numbers.

    python vm_fleet/compare_reports.py REFERENCE.docx OTHER.docx [--json out.json]

For each report: the chapters (Heading 1) and sections (Heading 2) in order, and per chapter the
figures, tables and images. Market-specific words are normalised before comparing: the market name
('Agricultural Biostimulants' / 'Aquaculture Testing & Diagnostics' -> <M>), its segment dimension
labels (-> <TYPE> / <DEMAND>) and country names (-> <COUNTRY>), so 'Saudi Arabia: Market by Crop Type'
and 'Egypt: Market by Test Type' line up. What differs after that is a real structural difference: a
chapter or section one report has and the other lacks, or a different number of charts or tables.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

import docx
from docx.oxml.ns import qn

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from AiAgentsFleet.cmi_report.groups import MEMBERS  # noqa: E402

COUNTRIES = sorted({c for cs in MEMBERS.values() for c in cs} | {"UAE", "United Arab Emirates"}, key=len, reverse=True)
_CAPTION = re.compile(r"^\s*(Figure|Table)\s+\d+(?:\.\d+)?:\s*(.*)$")


def _market_root(title: str) -> str:
    m = re.match(r"\s*(?:The\s+)?(.+?)\s+Market\b", title or "")
    return m.group(1).strip() if m else ""


def _norm(text: str, root: str, dims: list[tuple[str, str]]) -> str:
    t = text.strip()
    if root:
        t = re.sub(re.escape(root), "<M>", t, flags=re.I)
    for label, tag in dims:
        if label:
            t = re.sub(rf"\b{re.escape(label)}\b", tag, t, flags=re.I)
    for c in COUNTRIES:
        t = re.sub(rf"\b{re.escape(c)}\b", "<COUNTRY>", t)
    t = re.sub(r"(<COUNTRY>)(\s*(?:,|and|&)\s*<COUNTRY>)+", r"\1", t)
    t = re.sub(r"\b(19|20)\d\d[AEF]?\b", "<YEAR>", t)
    t = re.sub(r"^\s*\d+(?:\.\d+)*\s+", "", t)              # section numbers
    return re.sub(r"\s+", " ", t)


def outline(path: Path, dims: list[tuple[str, str]] | None = None) -> dict:
    d = docx.Document(str(path))
    body = d.element.body
    title = next((p.text for p in d.paragraphs if p.style.name in ("Title",) or "Outlook" in p.text), "")
    root = _market_root(title)
    dims = dims or []
    chapters: list[dict] = []
    cur = {"title": "(front matter)", "sections": [], "figures": [], "tables": 0, "images": 0}
    chapters.append(cur)
    for el in body.iterchildren():
        if el.tag == qn("w:p"):
            st = el.find(".//" + qn("w:pStyle"))
            style = st.get(qn("w:val")) if st is not None else ""
            text = el.xpath("string(.)")
            if el.findall(".//" + qn("w:drawing")):
                cur["images"] += len(el.findall(".//" + qn("w:drawing")))
            if style in ("Heading1", "Heading 1"):
                cur = {"title": _norm(text, root, dims), "sections": [], "figures": [], "tables": 0, "images": 0}
                chapters.append(cur)
            elif style in ("Heading2", "Heading 2"):
                cur["sections"].append(_norm(text, root, dims))
            else:
                mt = _CAPTION.match(text)
                if mt and mt.group(1) == "Figure":
                    cur["figures"].append(_norm(mt.group(2), root, dims))
        elif el.tag == qn("w:tbl"):
            cur["tables"] += 1
    return {"file": str(path), "market_root": root, "chapters": chapters,
            "totals": {k: sum(len(c[k]) if isinstance(c[k], list) else c[k] for c in chapters)
                       for k in ("sections", "figures", "tables", "images")}}


def _dims_from_model(path: Path | None) -> list[tuple[str, str]]:
    if not path or not path.exists():
        return []
    m = json.loads(path.read_text(encoding="utf-8"))
    m = m.get("model", m)
    out = []
    for k, tag in (("type", "<TYPE>"), ("demand", "<DEMAND>")):
        d = (m.get("dimensions") or {}).get(k) or {}
        for key in ("singular", "plural", "label"):
            if d.get(key):
                out.append((d[key], tag))
    return sorted(out, key=lambda x: -len(x[0]))


def compare(a: dict, b: dict) -> dict:
    ta, tb = [c["title"] for c in a["chapters"]], [c["title"] for c in b["chapters"]]
    chapter_diff = [ln for ln in difflib.unified_diff(ta, tb, "reference", "other", lineterm="", n=0)
                    if not ln.startswith(("---", "+++", "@@"))]
    per = []
    bmap = {c["title"]: c for c in b["chapters"]}
    for ca in a["chapters"]:
        cb = bmap.get(ca["title"])
        row = {"chapter": ca["title"], "in_other": cb is not None,
               "sections": [len(ca["sections"]), len(cb["sections"]) if cb else None],
               "figures": [len(ca["figures"]), len(cb["figures"]) if cb else None],
               "tables": [ca["tables"], cb["tables"] if cb else None],
               "images": [ca["images"], cb["images"] if cb else None]}
        if cb:
            row["sections_only_in_reference"] = [s for s in ca["sections"] if s not in cb["sections"]]
            row["sections_only_in_other"] = [s for s in cb["sections"] if s not in ca["sections"]]
            row["figures_only_in_reference"] = [f for f in ca["figures"] if f not in cb["figures"]]
            row["figures_only_in_other"] = [f for f in cb["figures"] if f not in ca["figures"]]
        per.append(row)
    for cb in b["chapters"]:
        if cb["title"] not in {c["title"] for c in a["chapters"]}:
            per.append({"chapter": cb["title"], "in_reference": False, "sections": [None, len(cb["sections"])],
                        "figures": [None, len(cb["figures"])], "tables": [None, cb["tables"]],
                        "images": [None, cb["images"]]})
    return {"reference": a["file"], "other": b["file"], "totals": {"reference": a["totals"], "other": b["totals"]},
            "chapter_order_diff": chapter_diff, "chapters": per}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("reference")
    p.add_argument("other")
    p.add_argument("--ref-model", help="the reference's cmi_model.json / market JSON (segment labels)")
    p.add_argument("--other-model", help="the other report's cmi_model.json / market JSON")
    p.add_argument("--json", help="write the full comparison here")
    a = p.parse_args()
    ra = outline(Path(a.reference), _dims_from_model(Path(a.ref_model) if a.ref_model else None))
    rb = outline(Path(a.other), _dims_from_model(Path(a.other_model) if a.other_model else None))
    res = compare(ra, rb)
    if a.json:
        Path(a.json).write_text(json.dumps({"comparison": res, "reference": ra, "other": rb}, indent=1,
                                           ensure_ascii=False), encoding="utf-8")
    print(f"reference: {ra['market_root']}  {ra['totals']}")
    print(f"other:     {rb['market_root']}  {rb['totals']}")
    print("chapter order differences:", res["chapter_order_diff"] or "none")
    for r in res["chapters"]:
        flag = "" if r.get("in_other", True) and r.get("in_reference", True) else "  <-- only in one report"
        print(f"\n{r['chapter'][:80]}{flag}")
        print(f"   sections {r['sections']}  figures {r['figures']}  tables {r['tables']}  images {r['images']}")
        for k in ("sections_only_in_reference", "sections_only_in_other", "figures_only_in_reference",
                  "figures_only_in_other"):
            for x in r.get(k) or []:
                print(f"   {k.replace('_', ' ')}: {x[:100]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
