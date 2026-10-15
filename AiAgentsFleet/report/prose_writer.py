"""Optional LLM writing step: produces template-depth prose for each of the 15
whitepaper sections (named regulations, company developments, regional
dynamics, qualitative insights), grounded in

  * FACTS    -- this market's final, consistency-reconciled numbers, and
  * EVIDENCE -- fresh web-search snippets for that section's topic,

written in the tone of the template's own section text (style example).

Every generated string passes a number guard: if it contains a number that
is neither in FACTS nor verbatim in EVIDENCE, it is discarded and the
builder falls back to its data-only sentence for that slot. So the AI adds
words and qualitative specifics, never new figures.

Writes output/<slug>/prose.json (re-used by build_from_template.py until the
market's fields change).

Usage:
    python prose_writer.py --market-slug <slug> [--force]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import docx

REPORT_DIR = Path(__file__).resolve().parent
FLEET_DIR = REPORT_DIR.parent
REPO = FLEET_DIR.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO))

from me_engine.generation.config import GenConfig  # noqa: E402
from me_engine.generation.llm import GenLLM  # noqa: E402
from me_engine.generation.web_evidence import gather  # noqa: E402

from AiAgentsFleet.consistency import concentration_label  # noqa: E402
from narrative import build_narrative  # noqa: E402
import labels as dim_labels  # noqa: E402
from labels import similar_heading  # noqa: E402

TEMPLATE = REPO / "vitamin_c_market_whitepaper_v2.docx"
OUTPUT_ROOT = FLEET_DIR / "output"
REGIONS = ["North America", "Europe", "Asia Pacific", "Latin America", "Middle East & Africa"]

# section -> (fields used as FACTS, search queries, JSON schema hint)
SECTIONS: dict[int, tuple[list[str], list[str], str]] = {
    1: (["market_size", "drivers_restraints", "regional", "end_use_industry", "competitive_landscape"],
        ["{market} key trends", "{market} industry overview outlook"],
        '{"p1": <overview paragraph>, "p2": <growth drivers vs headwinds paragraph>, "callout": <one striking '
        'sentence>, "findings": [{"head": <short finding headline>, "text": <2-3 sentences>}, x3]}'),
    2: (["market_size", "regional", "end_use_industry"],
        ["{market} market size growth history", "{market} market growth outlook 2035"],
        '{"p1": <current size + historical trend paragraph>, "p2": <long-term growth outlook paragraph>, '
        '"callout": <one sentence takeaway>}'),
    3: (["drivers_restraints", "market_size"],
        ["{market} growth drivers", "{market} challenges restraints"],
        '{"p1": <balance of drivers vs restraints paragraph>, "drivers": <paragraph on the drivers>, '
        '"restraints": <paragraph on the restraints>}'),
    4: (["porters_five_forces", "competitive_landscape"],
        ["{market} barriers to entry competition suppliers buyers"],
        '{"p1": <overall attractiveness paragraph>, "forces": {"supplier_power": <1 sentence rationale>, '
        '"competitive_rivalry": <...>, "buyer_power": <...>, "threat_of_new_entrants": <...>, '
        '"threat_of_substitutes": <...>}}'),
    5: (["segmentation_grade_form", "market_size"],
        ["{market} product types segmentation", "{market} product forms formats"],
        '{"grades": <paragraph on the segments in by_grade_or_type>, "forms": <paragraph on the segments in by_form>}'),
    6: (["end_use_industry", "market_size"],
        ["{market} applications end-use demand"],
        '{"p1": <largest applications paragraph>, "p2": <fastest-growing application paragraph>}'),
    7: (["sub_segments", "market_size"],
        ["{market} fastest growing niche segments"],
        '{"p1": <intro paragraph on where growth concentrates among the sub-segments>, "bullets": [{"lead": <group name from by_top_application + colon>, "text": <1-2 sentences>}, '
        'one per application group]}'),
    8: (["derivatives_pricing", "market_size"],
        ["{market} pricing price trends", "{market} premium products value chain"],
        '{"p1": <value expansion across pricing paragraph>, "p2": <derivative/adjacent products paragraph>, '
        '"callout": <one sentence on the value uplift>, "pricing": <multi-tier pricing paragraph>}'),
    9: (["regional", "market_size"],
        ["{market} North America Europe market", "{market} Asia Pacific Latin America Middle East Africa market"],
        '{"callout": <short punchy regional tagline>, "p1": <regional divide paragraph>, "regions": '
        '{"<region name>": {"descriptor": <3-6 word title e.g. A Mature Consumption-Driven Market>, '
        '"para": <2-3 sentences>, "bullets": [<2-4 bullets about THIS region only: one on its leading '
        'application_mix_pct item, one on its leading countries, plus drivers from EVIDENCE>]}, ... for each region in FACTS}}'),
    10: (["competitive_landscape", "market_size"],
         ["{market} leading companies", "{market} acquisitions partnerships product launches"],
         '{"p1": <concentration + leaders paragraph>, "companies": {"<company name>": <1-2 sentence positioning, '
         'without restating its share>, '
         '... for the top 5 companies in FACTS}, "p2": <closing paragraph on competitive dynamics>}'),
    11: (["regulation", "market_size"],
         ["{market} regulations approval requirements", "{market} regulatory framework compliance"],
         '{"p1": <regulation as cost layer paragraph>, "bullets": [<4-5 bullets on regulatory complexity, '
         'naming real regulations/agencies from EVIDENCE>]}'),
    12: (["willingness_to_pay", "market_size"],
         ["{market} customer buying behavior", "{market} buyers price sensitivity adoption"],
         '{"p1": <buyer behavior shift paragraph>, "p2": <spending segmentation paragraph>, '
         '"bullets": [<3 bullets on behavior, pricing power, innovation>]}'),
    13: (["investment_priorities", "market_size"],
         ["{market} investment opportunities"],
         '{"p1": <investment pathways paragraph>, "bullets": [<3 bullets on the highest-potential opportunities>]}'),
    14: (["scenarios", "market_size", "drivers_restraints"],
         ["{market} outlook risks uncertainty"],
         '{"p1": <scenario framing paragraph>, "bear": <1-2 sentence bear rationale>, "base": <1-2 sentence base '
         'rationale>, "bull": <1-2 sentence bull rationale> -- each rationale only, without the case name, value '
         'or CAGR (the heading shows them)}'),
    15: (["market_size", "regional", "end_use_industry", "competitive_landscape", "drivers_restraints"],
         [],
         '{"p1": <thesis intro paragraph>, "thesis": [{"lead": <short bold heading + colon>, "text": '
         '<1-2 sentences>}, x4], "concluding": <concluding view paragraph>}'),
}

_SYSTEM = (
    "You are a senior industry analyst writing one section of a market whitepaper. Match the tone, structure "
    "and length of the STYLE EXAMPLE (a section from a Vitamin C whitepaper), but write ONLY about the market "
    "named. Rules:\n"
    "1. Every number you write (sizes, %, CAGRs, shares, prices, years, counts) must come from FACTS (rounded "
    "the same way they appear there) or appear verbatim in EVIDENCE. Never invent or estimate numbers.\n"
    "2. Use EVIDENCE for qualitative specifics: named regulations, agencies, programmes, companies, "
    "technologies, country dynamics. If EVIDENCE has nothing relevant, stay general rather than invent.\n"
    "3. Never contradict FACTS (which segment/region/company is largest or fastest, growth direction).\n"
    "4. Never mention Vitamin C or reuse facts from the STYLE EXAMPLE.\n"
    "5. Plain prose. No markdown, no URLs, no source citations, no 'according to'.\n"
    "6. Be selective like the STYLE EXAMPLE: use only the 2-4 most telling numbers per paragraph (never list "
    "every item), keep paragraphs to 60-110 words and bullets to 1-2 sentences, and spend the words on "
    "insight -- why it matters, what drives it, named real-world specifics from EVIDENCE.\n"
    "7. Stay on THIS section's topic. Sections 1, 2, 14 and 15 own the headline market size, forecast and "
    "CAGR; in every other section do not restate them -- open with the section's own subject.\n"
    "8. Describe segments with the names in headline.dimension_labels (e.g. call them 'product types' or "
    "'species' as labelled there), never as 'grades', 'forms' or 'industries' unless that is the label.\n"
    "9. Describe the competitive structure with exactly the term in headline.derived.competition_structure.\n"
    "10. Make a comparative claim (largest, fastest, highest share globally) only when FACTS show it. Never "
    "attribute a global figure (e.g. the compliance-cost range) to a single region.\n"
    "11. A bullet's text must not repeat its own heading/lead (name, rating, share, value or CAGR already "
    "shown there). Headings must not reuse or paraphrase any in ALREADY USED HEADINGS.\n"
    "Return ONLY valid JSON matching SCHEMA."
)

_LABEL_SYSTEM = (
    "You name the dimension that each list of market segments measures. For each list return a label of 1-3 "
    "words in Title Case that says what the items ARE -- e.g. 'Product Type', 'Shrimp Species', 'Deployment "
    "Model', 'Application Method', 'Crop Type', 'End-Use Industry', 'Feed Type', 'Farming System'. Use 'Grade' "
    "or 'Form' only if the items really are grades or physical forms. Do not include the market name.\n"
    'Return ONLY JSON: {"grade": {"singular": ..., "plural": ...}, "form": {...}, "application": {...}, '
    '"sub_segment_group": {...}} with a key for each list given.'
)

# keys whose strings are headings (checked against headings used earlier in the report)
_HEAD_KEYS = {"head", "lead", "callout", "descriptor"}
_CONC_RE = re.compile(r"\b(highly concentrated|moderately concentrated|fragmented)\b", re.I)
_COMPETITION_RE = re.compile(r"compet|player|compan|landscape|leader|rival|share", re.I)

_NUM_RE = re.compile(r"(?<![A-Za-z])\d[\d,]*(?:\.\d+)?")


# --------------------------------------------------------------------------- #
# number guard
# --------------------------------------------------------------------------- #
def _numbers_in(text: str) -> list[tuple[float, int]]:
    out = []
    for m in _NUM_RE.finditer(text or ""):
        s = m.group().replace(",", "").rstrip(".")
        try:
            out.append((float(s), len(s.split(".")[1]) if "." in s else 0))
        except ValueError:
            continue
    return out


def _collect_values(obj, out: list[float]) -> None:
    if isinstance(obj, dict):
        for v in obj.values():
            _collect_values(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_values(v, out)
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        out.append(float(obj))
    elif isinstance(obj, str):
        out.extend(v for v, _ in _numbers_in(obj))


class NumberGuard:
    def __init__(self, facts: dict, evidence: list[str]):
        vals: list[float] = []
        _collect_values(facts, vals)
        expanded = set()
        for v in vals:
            expanded.update({v, v / 1000.0, v * 100.0, v / 100.0})
        self.facts = sorted(expanded)
        self.evidence = {v for s in evidence for v, _ in _numbers_in(s)}

    def ok_number(self, x: float, decimals: int) -> bool:
        if decimals == 0 and (x <= 12 or 1990 <= x <= 2040):   # small counts, years
            return True
        if x in self.evidence:
            return True
        tol = 0.5 * 10 ** (-decimals) + 1e-9
        return any(abs(x - a) <= tol for a in self.facts)

    def ok(self, text: str) -> bool:
        return all(self.ok_number(x, d) for x, d in _numbers_in(text))


class TextChecks:
    """Everything a generated string must pass besides the number guard:
    it may not restate the headline size/forecast outside the sections that
    own it, may not contradict the competition-structure label, and a
    heading may not repeat one already used earlier in the report."""

    def __init__(self, section: int, n: dict, conc: str, used_heads: list[str]):
        self.section = section
        self.market = n["market"]
        self.conc = conc
        self.used_heads = used_heads
        self.fc = n["fc_val_fmt"].replace("USD ", "")
        self.base = n["base_val_fmt"].replace("USD ", "")
        self.cagr = f"{n['cagr']:.1f}%"

    def problem(self, text: str, key: str | None) -> str | None:
        low = text.lower()
        if self.section not in (1, 2, 14, 15) and (
                self.fc in low or (self.base in low and self.cagr in low)):
            return "restates headline market size"
        if _COMPETITION_RE.search(text):
            for m in _CONC_RE.finditer(text):
                if m.group(1).lower() != self.conc:
                    return f"says '{m.group(1)}' but competition is {self.conc}"
        if key in _HEAD_KEYS and any(similar_heading(text, h, self.market) for h in self.used_heads):
            return "repeats an earlier heading"
        return None


def _clean(obj, guard: NumberGuard, dropped: list[str], checks: TextChecks | None = None, key: str | None = None):
    """Recursively keep only strings that pass the number guard and checks."""
    if isinstance(obj, str):
        txt = obj.strip()
        if not txt or "vitamin c" in txt.lower():
            dropped.append(txt[:80])
            return None
        if not guard.ok(txt):
            dropped.append(txt[:80])
            return None
        why = checks.problem(txt, key) if checks else None
        if why:
            dropped.append(f"[{why}] {txt[:80]}")
            return None
        return txt
    if isinstance(obj, list):
        out = [_clean(v, guard, dropped, checks, key) for v in obj]
        return [v for v in out if v not in (None, {}, [])]
    if isinstance(obj, dict):
        out = {k: _clean(v, guard, dropped, checks, k) for k, v in obj.items()}
        # a {"head"/"lead": ..., "text": ...} pair is only usable whole
        if ({"head", "text"} <= obj.keys() or {"lead", "text"} <= obj.keys()) and None in out.values():
            return None
        return {k: v for k, v in out.items() if v not in (None, {}, [])}
    return None


# --------------------------------------------------------------------------- #
# inputs
# --------------------------------------------------------------------------- #
def _template_sections() -> dict[int, str]:
    d = docx.Document(str(TEMPLATE))
    secs: dict[int, list[str]] = {}
    cur = None
    for el in d.element.body.iterchildren():
        txt = el.xpath("string(.)").strip()
        if not txt:
            continue
        m = re.fullmatch(r"SECTION\s*(\d+)", txt)
        if m:
            cur = int(m.group(1))
            secs[cur] = []
            continue
        if txt.startswith("IMPORTANT NOTICE"):
            break
        if cur and not txt.startswith(("DATA CONFIDENCE", "Figure ", "Table ")):
            secs[cur].append(txt)
    return {k: "\n".join(v) for k, v in secs.items()}


def _round(obj):
    if isinstance(obj, dict):
        return {k: _round(v) for k, v in obj.items() if not k.startswith("_") and k != "type"}
    if isinstance(obj, list):
        return [_round(v) for v in obj]
    if isinstance(obj, float):
        return round(obj, 1) if abs(obj) >= 10 else round(obj, 2)
    return obj


def _headline(n: dict, labels: dict) -> dict:
    f = n["fields"]
    regions = (f.get("regional") or {}).get("regions") or {}
    comps = (f.get("competitive_landscape") or {}).get("companies") or []
    areas = (f.get("regulation") or {}).get("compliance_areas") or []
    apps = sorted((f.get("end_use_industry") or {}).get("applications") or [],
                  key=lambda a: a.get("share_pct_of_market", 0), reverse=True)
    derived = {
        "total_driver_bps": round(n["total_driver_bps"]), "total_restraint_bps": round(n["total_restraint_bps"]),
        "top3_company_share_pct": round(sum(c.get("share_pct", 0) for c in comps[:3]), 1),
        "named_company_share_pct": round(sum(c.get("share_pct", 0) for c in comps), 1),
        "named_company_count": len(comps),
        "competition_structure": concentration_label(sum(c.get("share_pct", 0) for c in comps[:3])),
        "largest_region": n["leader_region"][0], "fastest_region": n["fastest_region"][0],
        "largest_application": apps[0]["name"] if apps else None,
        "fastest_application": n["fastest_app"].get("name"),
        "region_value_added_2025_2030_usd_mn": {
            r: round((d.get("forecast_value_usd_mn", 0) or 0) - (d.get("value_usd_mn", 0) or 0), 1)
            for r, d in regions.items()},
        "compliance_cost_range_pct_of_cogs": [
            round(min((a.get("cost_pct_of_cogs_low", 0) for a in areas), default=0), 1),
            round(max((a.get("cost_pct_of_cogs_high", 0) for a in areas), default=0), 1)],
    }
    return {
        "derived": derived,
        "dimension_labels": {
            "segmentation_grade_form.by_grade_or_type": labels["grade"]["plural"],
            "segmentation_grade_form.by_form": labels["form"]["plural"],
            "end_use_industry.applications": labels["application"]["plural"],
            "sub_segments.by_top_application": labels["sub_segment_group"]["plural"],
            "regional.regions.*.application_mix_pct": labels["region_mix"]["plural"],
        },
        "market": n["market"], "base_year": n["base_year"], "forecast_year": n["forecast_year"],
        "base_value": n["base_val_fmt"], "forecast_value": n["fc_val_fmt"],
        "incremental_value": n["incremental_fmt"], "cagr_pct": round(n["cagr"], 1),
        "growth_multiple_x": round(n["fc_val"] / n["base_val"], 1) if n["base_val"] else None,
    }


# Bump when the prompt rules or text checks change, so cached prose.json from
# an older version is rewritten instead of re-used (build_from_template.py
# ignores prose.json from another version).
PROSE_VERSION = 2


def fields_hash(fields: dict) -> str:
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()[:16]


def is_current(prose: dict, fields: dict) -> bool:
    return prose.get("fields_hash") == fields_hash(fields) and prose.get("version") == PROSE_VERSION


def dimension_labels(llm: GenLLM, market: str, fields: dict) -> dict:
    """One LLM call naming what each segment list measures (see labels.py)."""
    names = {k: v for k, v in dim_labels.segment_names(fields).items() if v}
    if not names:
        return {}
    user = f"MARKET: {market}\nLISTS:\n{json.dumps(names, ensure_ascii=False, indent=1)}"
    try:
        raw = llm.complete_json(_LABEL_SYSTEM, user) or {}
    except Exception as exc:  # noqa: BLE001
        print(f"  [labels] LLM failed: {exc}")
        return {}
    return {k: v for k, v in raw.items() if k in dim_labels.DIMENSIONS and dim_labels._valid(v)}


def _headings(section: dict) -> list[str]:
    """Heading-like strings of one generated section (finding heads, thesis
    leads, callouts, region descriptors)."""
    out = []
    def walk(obj, key=None):
        if isinstance(obj, str) and key in _HEAD_KEYS:
            out.append(obj)
        elif isinstance(obj, dict):
            for k, v in obj.items():
                walk(v, k)
        elif isinstance(obj, list):
            for v in obj:
                walk(v, key)
    walk(section)
    return out


def write(slug: str, force: bool = False) -> Path:
    market_dir = OUTPUT_ROOT / slug
    data = json.loads((market_dir / "whitepaper_fields.json").read_text(encoding="utf-8"))
    out_path = market_dir / "prose.json"
    h = fields_hash(data["fields"])
    if out_path.exists() and not force:
        old = json.loads(out_path.read_text(encoding="utf-8"))
        if is_current(old, data["fields"]):
            print(f"prose.json up to date for {slug}")
            return out_path

    n = build_narrative(data)
    f = n["fields"]
    market = data["market_name"]
    styles = _template_sections()
    llm = GenLLM(GenConfig.load(REPO), cache_path=REPO / "output" / ".gen_cache.db")
    scope = data.get("definition_inclusion_exclusion") or data.get("market_definition") or {}

    raw_labels = dimension_labels(llm, market, f)
    labels = dim_labels.resolve(f, raw_labels, market)
    print(f"  [labels] " + ", ".join(f"{d}={labels[d]['plural']}" for d in (*dim_labels.DIMENSIONS, "region_mix")))
    comps = (f.get("competitive_landscape") or {}).get("companies") or []
    conc = concentration_label(sum(c.get("share_pct", 0) for c in comps[:3]))

    result = {"market_name": market, "fields_hash": h, "version": PROSE_VERSION,
              "labels": raw_labels, "sections": {}, "dropped": {}}
    used_heads: list[str] = []
    for num, (keys, queries, schema) in SECTIONS.items():
        facts = {"headline": _headline(n, labels), **{k: _round(f.get(k, {})) for k in keys}}
        if "market_size" in facts and not (facts["market_size"].get("history_check") or {}).get("ok"):
            # history that failed consistency's history_check is never shown to the writer
            facts["market_size"] = {k: v for k, v in facts["market_size"].items() if k != "historical_usd_mn"}
        if num == 9:
            facts["regions_in_report"] = [r for r in REGIONS if r in (f.get("regional", {}).get("regions") or {})]
        if num == 10:
            facts["top5_companies"] = [c["name"] for c in (f.get("competitive_landscape", {}).get("companies") or [])[:5]]
        evidence: list[str] = []
        for q in queries:
            try:
                evidence += gather([q.format(market=market)], per_query=5, cap=6)
            except Exception as exc:  # noqa: BLE001
                print(f"  [sec {num}] search failed: {exc}")
        user = (
            f"MARKET: {market}\nSCOPE: {json.dumps(scope, ensure_ascii=False)}\n\n"
            f"FACTS (the only allowed source of numbers):\n{json.dumps(facts, ensure_ascii=False)}\n\n"
            f"EVIDENCE (web snippets, for qualitative specifics):\n"
            + ("\n".join(f"- {e}" for e in evidence) or "(none)")
            + "\n\nALREADY USED HEADINGS (earlier sections -- do not reuse or paraphrase):\n"
            + ("\n".join(f"- {x}" for x in used_heads) or "(none)")
            + f"\n\nSTYLE EXAMPLE (tone/length only -- do not reuse its facts):\n{styles.get(num, '')}\n\n"
            f"SCHEMA:\n{schema}"
        )
        try:
            raw = llm.complete_json(_SYSTEM, user)
        except Exception as exc:  # noqa: BLE001
            print(f"  [sec {num}] LLM failed: {exc}")
            continue
        guard = NumberGuard(facts, evidence)
        dropped: list[str] = []
        clean = _clean(raw, guard, dropped, TextChecks(num, n, conc, used_heads)) or {}
        result["sections"][str(num)] = clean
        used_heads += _headings(clean)
        if dropped:
            result["dropped"][str(num)] = dropped
        print(f"  [sec {num}] {len(evidence)} snippets; kept {sum(1 for _ in _iter_strings(clean))} text blocks"
              + (f", dropped {len(dropped)} (failed number/consistency checks)" if dropped else ""))

    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return out_path


def _iter_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_strings(v)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--market-slug", required=True)
    p.add_argument("--force", action="store_true")
    args = p.parse_args()
    print(f"Wrote {write(args.market_slug, args.force)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
