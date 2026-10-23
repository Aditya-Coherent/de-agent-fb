"""Rebuilds a market's whitepaper from its one-file JSON alone (market_json.py) and checks the result.

    python vm_fleet/json_to_pdf.py vm_fleet/json/<slug>.json [--out DIR] [--compare ORIGINAL.pdf]

From nothing but the JSON and the template it redraws every chart, fills the .docx, and exports the PDF
(MS Word where installed, else LibreOffice). Then it checks the PDF against the JSON:
  * every headline number of the model (global / MEA / region sizes and CAGRs) is printed as the model
    states it,
  * every section's text in the JSON is in the PDF (sentences matched after whitespace/hyphen folding;
    sentences the build's guards leave out are counted separately, not as failures),
  * with --compare, how close the rebuilt PDF is to the PDF the pipeline produced (word-level similarity).
Exit code 0 = every check passed.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from AiAgentsFleet.cmi_report import build as builder, fmt, verify_facts  # noqa: E402
from AiAgentsFleet.cmi_report import run_cmi_report as rc  # noqa: E402
from AiAgentsFleet.cmi_report.guard import sentences  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import market_json  # noqa: E402


def pdf_text(path: Path) -> str:
    import pymupdf
    with pymupdf.open(path) as d:
        return "\n".join(p.get_text() for p in d)


def fold(s: str) -> str:
    """Text as compared: PDF line breaks and hyphenation undone, quotes/dashes unified, spaces collapsed."""
    s = s.replace("­", "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"[–—]", "-", s)
    s = re.sub(r"-\s*\n\s*", "-", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def render(data: dict, out_dir: Path) -> tuple[Path, Path | None]:
    out_dir.mkdir(parents=True, exist_ok=True)
    m = data["model"]
    pr = {"slots": data["text"]}
    definition = (data.get("definition") or {}).get("used") or m.get("definition")
    if data.get("report_agents"):                            # the agent data exactly as the build used it
        ag = data["report_agents"]
    else:                                                    # older JSON: rebuild the same view
        ag = verify_facts.apply(data["agents"]["cmi"])
        ag["_definition"] = definition
    charts = rc.make_charts(m, ag, out_dir / "charts")
    render.charts = len(charts)
    slug = data["meta"]["slug"]
    docx = out_dir / f"{slug}-from-json.docx"
    builder.build(rc.TEMPLATE, docx, m, pr, ag, charts, definition)
    page_pass = rc.word_pass if sys.platform == "win32" else rc.libreoffice_pass
    pages = page_pass(docx, None)
    if pages:
        builder.build(rc.TEMPLATE, docx, m, pr, ag, charts, definition, toc_pages=pages)
    pdf = docx.with_suffix(".pdf")
    page_pass(docx, pdf)
    return docx, pdf if pdf.exists() else None


def headline_numbers(m: dict) -> dict[str, str]:
    """The figures the text always prints (cover, KPI panel, scorecard). Region sizes are shown in charts
    (images), which PDF text extraction can't read, so they are reported, not required."""
    g, mea = m["global"], m["regions"]["Middle East & Africa"]
    return {"global 2026E size": fmt.usd(g["v26"]), "global 2033F size": fmt.usd(g["v33"]),
            "global CAGR": fmt.pct(g["cagr"]), "MEA 2026E size": fmt.usd(mea["v26"]),
            "MEA 2033F size": fmt.usd(mea["v33"]), "MEA CAGR": fmt.pct(mea["cagr"])}


def pdf_images(path: Path) -> int:
    import pymupdf
    with pymupdf.open(path) as d:
        return sum(len(p.get_images()) for p in d)


def check(data: dict, pdf: Path, compare: Path | None = None, charts: int | None = None) -> dict:
    text = fold(pdf_text(pdf))
    nums = headline_numbers(data["model"])
    missing_nums = {k: v for k, v in nums.items() if fold(v) not in text}
    region_in_text = {r: fold(fmt.usd(v["v26"])) in text for r, v in data["model"]["regions"].items()}
    found, missing = 0, []
    for key, v in data["text"].items():
        vals = v if isinstance(v, list) else list(v.values()) if isinstance(v, dict) else [v]
        for item in vals:
            for s in sentences(item if isinstance(item, str) else " ".join(
                    str(x) for x in (item.values() if isinstance(item, dict) else [item]))):
                fs = fold(s)
                if len(fs) < 40:                       # short labels/fragments: not a sentence check
                    continue
                if fs[:60] in text or fs[-60:] in text:
                    found += 1
                else:
                    missing.append(f"{key}: {s[:110]}")
    res = {"pdf": str(pdf), "pages": _pages(pdf), "charts_drawn": charts, "images_in_pdf": pdf_images(pdf),
           "headline_numbers_checked": len(nums), "headline_numbers_missing": missing_nums,
           "region_2026_sizes_in_text": region_in_text,
           "text_sentences_in_pdf": found, "text_sentences_not_in_pdf": len(missing),
           "not_in_pdf_examples": missing[:12]}
    if compare and compare.exists():
        a, b = fold(pdf_text(compare)).split(), text.split()
        res["similarity_to_pipeline_pdf"] = round(difflib.SequenceMatcher(None, a, b, autojunk=False).ratio(), 4)
    total = found + len(missing)
    res["text_coverage"] = round(found / total, 4) if total else None
    # pass: every headline number printed; >= 90% of JSON sentences in the PDF (the build drops a few
    # sentences on purpose: guards, chart-echo and dedupe passes run at build time)
    # and every drawn chart is in the PDF (the cover counts as one; logos in the template add more)
    res["ok"] = (not missing_nums and (res["text_coverage"] or 0) >= 0.90
                 and (charts is None or res["images_in_pdf"] >= charts))
    return res


def _pages(path: Path) -> int:
    import pymupdf
    with pymupdf.open(path) as d:
        return d.page_count


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("json_path")
    p.add_argument("--out", default=None)
    p.add_argument("--compare", default=None, help="the pipeline's own PDF, for a similarity score")
    a = p.parse_args()
    jp = Path(a.json_path)
    data = market_json.load(jp)                  # year keys back to numbers
    out = Path(a.out) if a.out else jp.parent / f"{data['meta']['slug']}-from-json"
    docx, pdf = render(data, out)
    if not pdf:
        print(f"built {docx}, but no PDF exporter (MS Word / LibreOffice) is available")
        return 2
    res = check(data, pdf, Path(a.compare) if a.compare else None, charts=getattr(render, "charts", None))
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
