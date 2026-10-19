"""Audits a finished CMI whitepaper against the report standard:
internal consistency, factual-accuracy labelling, and relevance.

Three kinds of checks, reported separately:
  * computed -- exact checks on the model and the .docx (sums, CAGRs, share
    claims vs growth, ties, blank cells, announced counts, profiles, labels);
  * ai_review -- one LLM review per market for judgement calls (missing major
    countries, parent/subsidiary double counting, segments vs definition,
    regulator currency, recommendations without a finding). These are
    assessments, not proofs;
  * plausibility_review -- a second LLM prompt: a sceptical industry analyst
    reads the finished report and the master data sheet and lists what is
    implausible, each with a severity.

With --gate, the blocking release gate (PLAN-08) decides whether the report
can go to a client: {"ready", "blockers", "warnings"}; any blocker -> not
ready, and the command exits non-zero. It also writes the internal QA report
<slug>-internal-qa.md that the reviewer reads to decide whether to ship.

    python AiAgentsFleet/cmi_report/audit.py --market-slug <slug> [--no-ai] [--gate] [--market-dir DIR]
Writes output/<slug>/audit_report.json (and <slug>-internal-qa.md with --gate).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FLEET = HERE.parent
ROOT = FLEET.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import docx  # noqa: E402

from AiAgentsFleet.cmi_report import fmt, model  # noqa: E402
from AiAgentsFleet.cmi_report.guard import sentences  # noqa: E402

_NUMWORD = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def _doc_texts(path: Path) -> tuple[list[str], list[list[list[str]]]]:
    d = docx.Document(str(path))
    paras = [p.text for p in d.paragraphs if p.text.strip()]
    tables = [[[c.text.strip() for c in r.cells] for r in t.rows] for t in d.tables]
    return paras, tables


# --------------------------------------------------------------------------- #
# computed checks
# --------------------------------------------------------------------------- #
def check_math(m: dict) -> list[str]:
    out = []
    tol = lambda a, b: abs(a - b) <= max(0.5, abs(b) * 0.002)  # noqa: E731
    g = m["global"]
    for y in m["years"]:
        s = sum(r["series"][y] for r in m["regions"].values())
        if not tol(s, g["series"][y]):
            out.append(f"regions sum to {s:.1f} in {y}, global is {g['series'][y]:.1f}")
    mea = m["regions"]["Middle East & Africa"]
    for k in ("v26", "v33"):
        s = sum(r[k] for r in m["subregions"].values())
        if not tol(s, mea[k]):
            out.append(f"Middle East + Africa = {s:.1f} ({k}), MEA = {mea[k]:.1f}")
    for reg, d in m["countries"].items():
        parent = m["regions"].get(reg) or m["subregions"].get(reg)
        for k in ("v26", "v33"):
            s = sum(c[k] for c in d.values())
            if not tol(s, parent[k]):
                out.append(f"{reg} countries sum to {s:.1f} ({k}), region is {parent[k]:.1f}")
    for geo, dims in m["segments"].items():
        parent = (g if geo == "Global" else m["subregions"].get(geo)
                  or next((d[geo] for d in m["countries"].values() if geo in d), None))
        for dim, segs in dims.items():
            if not segs or not m["sections"][dim]["show"]:
                continue
            for k in ("v26", "v33"):
                s = sum(x[k] for x in segs.values())
                if parent and not tol(s, parent[k]):
                    out.append(f"{geo} {dim} segments sum to {s:.1f} ({k}), total is {parent[k]:.1f}")
    # an aggregate's CAGR lies within the range of its components' CAGRs
    def within(name, agg, parts):
        cs = [p["cagr"] for p in parts]
        if cs and not (min(cs) - 0.05 <= agg["cagr"] <= max(cs) + 0.05):
            out.append(f"{name}: CAGR {agg['cagr']:.2f}% is outside its components' range {min(cs):.2f}-{max(cs):.2f}%")
    within("Global (regions)", g, list(m["regions"].values()))
    within("Middle East & Africa (sub-regions)", mea, list(m["subregions"].values()))
    for reg, d in m["countries"].items():
        within(f"{reg} (countries)", m["regions"].get(reg) or m["subregions"].get(reg), list(d.values()))
    # CAGR and increment arithmetic on every node
    nodes = [("global", g), *m["regions"].items(), *m["subregions"].items(),
             *[(c, v) for d in m["countries"].values() for c, v in d.items()]]
    for name, n in nodes:
        if abs(model.cagr(n["v26"], n["v33"]) - n["cagr"]) > 0.05:
            out.append(f"{name}: CAGR {n['cagr']:.2f}% does not match its 2026/2033 values")
        if abs((n["v33"] - n["v26"]) - n["incr"]) > 0.05:
            out.append(f"{name}: incremental value is not 2033 minus 2026")
    return out


_SHARE = re.compile(r"\b(gain(?:s|ing)?|los(?:es|ing|e)|increas\w*|ris\w*|expand\w*|declin\w*|fall\w*|shrink\w*)"
                    r"\b[^.]{0,30}\bshare\b|\bshare\b[^.]{0,30}\b(ris\w*|increas\w*|gain\w*|fall\w*|declin\w*|"
                    r"shrink\w*|drop\w*)", re.I)


def check_share_claims(m: dict, paras: list[str]) -> list[str]:
    """'X gains share' must mean X grows faster than its parent, and vice versa."""
    out = []
    ents = {**{r: (v, m["global"]) for r, v in m["regions"].items()},
            **{r: (v, m["regions"]["Middle East & Africa"]) for r, v in m["subregions"].items()}}
    for d_reg, d in m["countries"].items():
        parent = m["regions"].get(d_reg) or m["subregions"].get(d_reg)
        ents.update({c: (v, parent) for c, v in d.items()})
    ents["MEA"] = ents["Middle East & Africa"]
    for p in paras:
        for sent in sentences(p):
            # judge each clause on its own: in "X is the largest, while Y's share falls" the claim is about Y
            for clause in re.split(r",\s*(?:while|whereas|but)\b|;", sent):
                mm = _SHARE.search(clause)
                if not mm:
                    continue
                up = bool(re.search(r"gain|increas|ris|expand", mm.group(0), re.I))
                # the entity named closest before the share wording is the claim's subject
                before = clause[: mm.start()] + " " + mm.group(0)
                hits = [(before.rfind(n), n) for n in ents
                        if re.search(rf"(?<![A-Za-z]){re.escape(n)}(?![A-Za-z])", before)]
                if not hits:
                    continue
                name = max(hits, key=lambda h: (h[0], len(h[1])))[1]
                v, parent = ents[name]
                if up and v["cagr"] < parent["cagr"] or (not up and v["cagr"] > parent["cagr"]):
                    out.append(f"'{clause.strip()[:110]}' -- {name} grows {fmt.pct(v['cagr'])} vs parent "
                               f"{fmt.pct(parent['cagr'])}")
    return out


def check_ties(m: dict) -> list[str]:
    """Rankings whose top two are within the materiality threshold (rules.TIE_PP /
    TIE_REL): equal at printed precision is a 'tie', otherwise a 'near-tie'."""
    from AiAgentsFleet.cmi_report import rules
    out = []
    groups = {"regions": m["regions"], "subregions": m["subregions"], **m["countries"]}
    for geo, dims in m["segments"].items():
        for dim, segs in dims.items():
            if segs and m["sections"][dim]["show"]:
                groups[f"{geo} {dim}"] = segs
    for gname, d in groups.items():
        for key, label in (("cagr", "fastest"), ("v33", "largest")):
            top = sorted(d, key=lambda n: -d[n][key])[:2]
            if len(top) == 2:
                a, b = top
                fa = fmt.pct(d[a][key]) if key == "cagr" else fmt.usd(d[a][key])
                fb = fmt.pct(d[b][key]) if key == "cagr" else fmt.usd(d[b][key])
                if fa == fb:
                    out.append(f"{gname}: '{label}' is a tie at {fa} between {a} and {b}")
                elif rules.tied(key, d[a][key], d[b][key]):
                    out.append(f"{gname}: '{label}' is a near-tie between {a} ({fa}) and {b} ({fb})")
    return out


def check_tables(tables) -> list[str]:
    out = []
    for ti, t in enumerate(tables):
        for r in t[1:]:
            for c in r:
                if c in ("", "-", "–", "—", "n/a"):
                    out.append(f"table {ti + 1} ('{t[0][0]}'), row '{r[0][:40]}': empty or '-' cell")
    return out


def check_counts(paras: list[str], tables, ag: dict) -> list[str]:
    """'Six trends', 'Five priority actions', 'Three-phase' must be delivered in full."""
    out = []
    delivered = {
        "trend": len((ag.get("dynamics") or {}).get("trends") or []),
        "phase": len((ag.get("strategy") or {}).get("phases") or []),
    }
    for p in paras + [t[0][0] for t in tables if t and t[0]]:
        for mm in re.finditer(r"\b(two|three|four|five|six|seven|eight|nine|ten)[\s-]+(?:\w+\s+){0,2}?"
                              r"(trend|phase|priority action|action|finding|driver|scenario)s?\b", p, re.I):
            n, what = _NUMWORD[mm.group(1).lower()], mm.group(2).lower()
            if what in delivered and delivered[what] and delivered[what] != n:
                out.append(f"'{mm.group(0)}' announced, {delivered[what]} delivered")
            if what in ("priority action", "action"):
                box = next((t for t in tables if t and t[0] and mm.group(0).lower() in t[0][0].lower()), None)
                if box:
                    items = len(re.findall(r"(?:^|\n)\s*\d+\.", box[0][0]))
                    if items and items != n:
                        out.append(f"'{mm.group(0)}' announced, {items} listed")
    return sorted(set(out))


def check_profiles(m: dict, paras: list[str]) -> list[str]:
    """Every country named in a table needs at least a short profile ('Country: ...'
    line, or a Chapter 4/5 profile heading)."""
    profiled = {u for v in m["profiled"].values() for u in v}
    starts = {p.split(":")[0].strip() for p in paras if ":" in p[:40]}
    table_countries = [c for d in m["countries"].values() for c in d if not c.startswith("Rest of")]
    missing = [c for c in table_countries if c not in profiled and c not in starts]
    return [f"{len(missing)} of {len(table_countries)} countries in the tables have no profile: " + ", ".join(missing)] \
        if missing else []


def check_exec_vs_body(paras: list[str]) -> list[str]:
    """Numbers in the executive summary must also appear in the body."""
    try:
        start = next(i for i, p in enumerate(paras) if re.match(r"^\s*0?1\s+Executive Summary", p))
        end = next(i for i, p in enumerate(paras) if i > start and re.match(r"^\s*0?2\s+", p))
    except StopIteration:
        return ["could not locate the executive summary"]
    body = " ".join(paras[end:])
    out = []
    for p in paras[start:end]:
        for tok in re.findall(r"[+$]?\$?\d[\d,.]*[BM%]?", p):
            t = tok.strip(".,")
            if re.search(r"\d", t) and len(t) > 2 and t not in body and not re.fullmatch(r"20\d\d[EF]?", t):
                out.append(f"'{t}' in the executive summary does not appear in the body")
    return sorted(set(out))


_EST = re.compile(r"\b(estimat\w*|forecast\w*|project\w*|CMI|expected|assess\w*|indicative|scenario)\b", re.I)


def check_labels(paras: list[str]) -> dict:
    """Share of number-bearing PARAGRAPHS (not headings, not the appendix) with no
    estimate/forecast/CMI label, and count of [to verify] / [unverified] markers."""
    body = []
    for p in paras:
        if "\t" in p:                       # contents-page line
            continue
        if re.match(r"\s*1?1\s+Appendix", p):
            break
        if re.match(r"^\s*\d+(\.\d+)?\s+\S", p) and len(p) < 90:      # a numbered heading
            continue
        body.append(p)
    with_num = [p for p in body if re.search(r"\$\d|\d%", p) and len(p) > 60]
    unlabelled = [s for s in with_num if not _EST.search(s)]
    markers = sum(len(re.findall(r"\[(?:to verify|unverified)\]", p, re.I)) for p in paras)
    return {"number_paragraphs": len(with_num), "without_estimate_label": len(unlabelled),
            "examples": [s[:110] for s in unlabelled[:5]], "to_verify_markers": markers}


# --------------------------------------------------------------------------- #
# AI review (judgement)
# --------------------------------------------------------------------------- #
_AI_SYSTEM = (
    "You are a senior market-research editor auditing a market whitepaper for relevance and factual risk. Be "
    "concrete and conservative: only flag what you are confident about, and say why. Return ONLY JSON."
)
_AI_SCHEMA = (
    '{"missing_major_countries": [{"country": <string>, "currently": <"in Rest of <region>" or "absent">, '
    '"why_it_matters": <1 sentence>}], '
    '"double_counted_companies": [{"companies": [<names>], "issue": <1 sentence>}], '
    '"segments_vs_definition": [{"segment_or_dimension": <string>, "issue": <1 sentence>}], '
    '"questionable_regulators": [{"country": <string>, "named": <string>, "issue": <1 sentence>}], '
    '"recommendations_without_finding": [{"recommendation": <short>, "issue": <1 sentence>}], '
    '"other_relevance_issues": [<1 sentence each>]}'
)


def _llm(llm=None):
    if llm is not None:
        return llm
    from me_engine.generation.config import GenConfig
    from me_engine.generation.llm import GenLLM
    return GenLLM(GenConfig.load(ROOT), cache_path=ROOT / "output" / ".gen_cache.db")


def ai_review(market_dir: Path, m: dict, ag: dict, paras: list[str], llm=None) -> dict:
    prose = json.loads((market_dir / "cmi_prose.json").read_text(encoding="utf-8")) \
        if (market_dir / "cmi_prose.json").exists() else {}
    traced = [f"{a.get('lead')} {a.get('text')}" for a in (prose.get("slots") or {}).get("priority_actions") or []
              if isinstance(a, dict)]
    facts = {
        "market": m["market"],
        "definition": m["definition"],
        "segment_dimensions": {m["dimensions"][d]["singular"]: list(m["segments"]["Global"][d])
                               for d in ("type", "demand") if m["sections"][d]["show"]},
        "countries_named_per_region": {r: [c for c in d] for r, d in m["countries"].items()},
        "region_shares_2026_pct": {r: round(v["v26"] / m["global"]["v26"] * 100, 1) for r, v in m["regions"].items()},
        "company_share_chart_global": [n for n, _ in m["vendor_chart_global"]],
        "company_share_chart_mea": [n for n, _ in m["vendor_chart_mea"]],
        "regulators_named": [(r.get("country"), r.get("regulator")) for r in (ag.get("regulation") or {}).get("snapshot") or []],
        "recommendations_with_their_findings": traced or (ag.get("strategy") or {}).get("priority_actions"),
        "stakeholder_moves": [s.get("priority_moves") for s in (ag.get("strategy") or {}).get("stakeholders") or []],
        "key_findings_text": [p for p in paras if len(p) > 80 and not re.match(r"^\s*\d+\s{2,}", p)][:12],
        "note": ("The global and MEA share charts are separate charts (MEA is a regional view), so the same company "
                 "appearing in both is not double counting; flag only a group listed twice WITHIN one chart."),
    }
    try:
        return _llm(llm).complete_json(_AI_SYSTEM, f"REPORT FACTS:\n{json.dumps(facts, ensure_ascii=False)}\n\n"
                                                   f"Audit it. Return JSON exactly in this shape:\n{_AI_SCHEMA}")
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


# --------------------------------------------------------------------------- #
# AI plausibility review (judgement, blocking when "high")
# --------------------------------------------------------------------------- #
FINDINGS = FLEET / "plans" / "FINDINGS-2026-10-15.md"
_PLAUS_CATEGORIES = ("number", "fact", "logic", "scope", "vendor", "regulation")
_PLAUS_SYSTEM = (
    "You are a sceptical senior industry analyst who knows the {market} well. A client is about to receive the "
    "market whitepaper below. Read it as that client's most critical expert would: check every number against "
    "the master data sheet and against what you know of this industry (sizes of countries and regions relative "
    "to each other, who the real companies are and what they make, which regulators and laws exist and when they "
    "came in), and check that each conclusion follows from the report's own data. Only list problems you are "
    "confident about, and quote the report exactly. Return ONLY JSON."
)
_PLAUS_SEVERITY = (
    "Severity:\n"
    "- high: a client expert would catch it and stop trusting the report -- a number that is materially wrong or "
    "contradicts the report's own data, a company/regulator/law/date that is wrong or invented, a segment or "
    "vendor outside the market's scope, a ranking or recommendation the data contradicts.\n"
    "- medium: questionable or unsupported, should be checked before shipping.\n"
    "- low: wording or presentation.\n"
)
_PLAUS_SCHEMA = ('{"items": [{"quote": <exact quote from the report, at most 20 words>, "issue": <1 sentence>, '
                 '"category": <"number"|"fact"|"logic"|"scope"|"vendor"|"regulation">, '
                 '"severity": <"high"|"medium"|"low">, "fix": <1 short sentence>}]}  -- at most 25 items, '
                 'all "high" items first, then "medium"; leave out "low" items if the list would be longer')


def _few_shot() -> str:
    """The per-market sections (B-F) of the 2026-10-15 review: what 'high' looked like in earlier reports."""
    try:
        text = FINDINGS.read_text(encoding="utf-8")
    except OSError:
        return ""
    a, b = text.find("## B."), text.find("## G.")
    return text[a:b if b > a else None].strip() if a >= 0 else ""


def master_sheet(m: dict) -> dict:
    """The master data sheet in compact form (USD mn; 2026E, 2033F, CAGR %)."""
    def r(v):
        return [round(v["v26"], 2), round(v["v33"], 2), round(v["cagr"], 2)]
    sec = m["sections"]
    return {
        "columns": [f"{model.Y0}E USD mn", f"{model.Y1}F USD mn", "CAGR %"],
        "definition": {k: m["definition"].get(k) for k in ("definition", "measures", "value_chain_level",
                                                          "included", "excluded") if m["definition"].get(k)},
        "global": {**dict(zip(("v26", "v33", "cagr"), r(m["global"]))), "base_2025": round(m["global"]["v25"], 2)},
        "regions": {k: r(v) for k, v in m["regions"].items()},
        "subregions": {k: r(v) for k, v in m["subregions"].items()},
        "countries": {reg: {c: r(v) for c, v in d.items()} for reg, d in m["countries"].items()},
        # every geography's segment split (Global, Middle East, Africa and each profiled country): the
        # client report no longer carries an appendix, so the reviewer needs them here to check segment claims
        "segments": {geo: {m["dimensions"][d]["singular"]: {n: r(v) for n, v in segs[d].items()}
                           for d in ("type", "demand") if sec[d]["show"] and segs.get(d)}
                     for geo, segs in m["segments"].items()},
        "vendor_share_global_pct": m.get("vendor_share_global") or [],
        "vendor_share_mea_pct": m.get("vendor_share_mea") or [],
        "headline_size_check": (m.get("size_check") or {}).get("status"),
    }


def _norm_items(raw) -> list[dict]:
    items = raw.get("items") if isinstance(raw, dict) else raw
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict) or not (it.get("issue") or it.get("quote")):
            continue
        sev = str(it.get("severity") or "").lower().strip()
        cat = str(it.get("category") or "").lower().strip()
        out.append({"quote": str(it.get("quote") or "")[:300], "issue": str(it.get("issue") or ""),
                    "category": cat if cat in _PLAUS_CATEGORIES else "fact",
                    # an unreadable severity is treated as high: the gate must not pass on a parse slip
                    "severity": sev if sev in ("high", "medium", "low") else "high",
                    "fix": str(it.get("fix") or "")})
    return out


def plausibility_review(m: dict, report_text: str, llm=None, max_chars: int = 90000) -> dict:
    """{"items": [{quote, issue, category, severity, fix}], "ran": bool[, "error"]}."""
    shots = _few_shot()
    user = (f"MARKET: {m['market']}\n\nMASTER DATA SHEET (every number the report may print):\n"
            f"{json.dumps(master_sheet(m), ensure_ascii=False)}\n\n"
            + (f"EXAMPLES OF REAL PROBLEMS FOUND IN EARLIER REPORTS (other runs; they show the kind and seriousness "
               f"of a 'high' issue -- judge this report on its own, do not copy them):\n{shots}\n\n" if shots else "")
            + f"{_PLAUS_SEVERITY}\nREPORT TEXT:\n{report_text[:max_chars]}\n\n"
            f"List the problems. Return JSON exactly in this shape (\"items\": [] if there are none):\n{_PLAUS_SCHEMA}")
    try:
        raw = _llm(llm).complete_json(_PLAUS_SYSTEM.format(market=m["market"]), user)
    except Exception as exc:  # noqa: BLE001
        return {"ran": False, "error": str(exc), "items": []}
    if not isinstance(raw, (dict, list)) or (isinstance(raw, dict) and not isinstance(raw.get("items"), list)):
        return {"ran": False, "error": f"unreadable answer: {str(raw)[:200]}", "items": []}
    return {"ran": True, "items": _norm_items(raw)}


# --------------------------------------------------------------------------- #
# release gate
# --------------------------------------------------------------------------- #
def doc_blocks(path: Path) -> list[dict]:
    """The document in body order: {"kind": "p"|"table", "style", "text", "level"}; a table's text is
    every cell, row by row. Empty paragraphs are kept (kind "p", text "")."""
    from docx.oxml.ns import qn
    d = docx.Document(str(path))
    out = []
    for el in d.element.body.iterchildren():
        if el.tag == qn("w:p"):
            st = el.find(".//" + qn("w:pStyle"))
            style = st.get(qn("w:val")) if st is not None else ""
            lv = re.fullmatch(r"Heading(\d)", style)
            out.append({"kind": "p", "style": style, "level": int(lv.group(1)) if lv else None,
                        "text": el.xpath("string(.)"), "image": el.find(".//" + qn("w:drawing")) is not None})
        elif el.tag == qn("w:tbl"):
            rows = [[c.xpath("string(.)").strip() for c in tr.findall(qn("w:tc"))] for tr in el.iter(qn("w:tr"))]
            cell_paras = [["".join(t.text or "" for t in p.iter(qn("w:t"))) for p in tc.iter(qn("w:p"))]
                          for tc in el.iter(qn("w:tc"))]
            out.append({"kind": "table", "style": "", "level": None, "rows": rows, "cell_paras": cell_paras,
                        "text": "\n".join(" | ".join(r) for r in rows)})
    return out


_LEFTOVER = re.compile(r"\[(?:to verify|unverified)\]|\bTBD\b|\blorem\b|\[insert|draft template|publication template",
                       re.I)
_DRONE = re.compile(r"\b(drones?|UAS|UAV|BVLOS)\b", re.I)
_INTERNAL = re.compile(r"\bgenerated sentences?\b|\bsweep\b|\bself-audit\b", re.I)
_REGISTER_ID = re.compile(r"\bregister IDs?\b", re.I)
_PUBLISHED = re.compile(r"\bpublished\s+(?:\d{4}\s+)?(?:global\s+)?(?:market\s+)?(?:estimates?|figures?)\b|"
                        r"\btriangulat\w*", re.I)
REVIEWS = ("size_check", "geo_review", "segment_review", "vendor_review", "fact_review")


def _sections(blocks: list[dict]):
    """Yields (block, h1_text, h2_text): the chapter and sub-section each block sits in."""
    h1 = h2 = ""
    for b in blocks:
        if b["level"] == 1:
            h1, h2 = b["text"].strip(), ""
        elif b["level"] == 2:
            h2 = b["text"].strip()
        yield b, h1, h2


def empty_heading_blocks(blocks: list[dict]) -> list[str]:
    """Headings with no body (build.heading_is_empty): followed directly, blank paragraphs aside, by a
    heading of the same or a higher level, or by the end."""
    from AiAgentsFleet.cmi_report.build import heading_is_empty
    out = []
    for k, b in enumerate(blocks):
        if b["level"] is None:
            continue
        nxt = next((x for x in blocks[k + 1:] if x["kind"] == "table" or x["text"].strip() or x.get("image")), None)
        if heading_is_empty(b["level"], b["text"], nxt["level"] if nxt else None, nxt["text"] if nxt else None,
                            nxt is None):
            out.append(b["text"].strip()[:80])
    return out


def key_conclusions_count(blocks: list[dict]) -> int | None:
    """Items in the 'Key conclusions' box; None when the document has no such box."""
    for b in blocks:
        if b["kind"] == "table" and len(b["rows"]) == 1 and len(b["rows"][0]) == 1:
            ps = [p.strip() for p in b["cell_paras"][0] if p.strip()]
            if ps and ps[0].lower().startswith("key conclusions"):
                return len(ps) - 1
    return None


def profiles_without_lead(m: dict, blocks: list[dict]) -> list[str]:
    """Every Chapter 4/5 country profile must open with a lead paragraph (its heading is followed by text)."""
    out = []
    for sub, units in (m.get("profiled") or {}).items():
        for unit in units:
            k = next((i for i, b in enumerate(blocks) if b["level"] == 3 and b["text"].strip() == unit), None)
            if k is None:
                out.append(f"{unit} ({sub}): no profile in the document")
                continue
            nxt = next((x for x in blocks[k + 1:] if x["kind"] == "table" or x["text"].strip() or x.get("image")), None)
            if nxt is None or nxt["kind"] != "p" or nxt["level"] is not None or nxt.get("image") \
                    or len(nxt["text"].split()) < 6:
                out.append(f"{unit} ({sub}): the profile has no lead sentence")
    return out


def unsourced_big_shares(m: dict, limit: float = 20.0) -> list[str]:
    """Vendor shares above `limit`% without a source quote. Before PLAN-05 (no m["vendor_sources"]) no
    vendor share has a source."""
    srcs = m.get("vendor_sources")
    out = []
    for label, key, skey in (("global", "vendor_share_global", "global"), ("MEA", "vendor_share_mea", "mea")):
        sourced = {s.get("vendor") for s in (srcs or {}).get(skey) or [] if isinstance(s, dict) and s.get("quote")}
        for n, s in m.get(key) or []:
            if str(n).lower() in ("others", "regional & other") or s is None:
                continue
            if float(s) > limit and n not in sourced:
                out.append(f"{n}: {fmt.pct(float(s))} of the {label} market, no source quote")
    return out


def release_gate(m: dict, blocks: list[dict], *, qa_report: dict, computed: dict, plausibility: dict | None,
                 fact_review: dict | None = None) -> dict:
    """{"ready": bool, "blockers": [...], "warnings": [...]}. Any blocker -> not ready."""
    blockers, warnings = [], []
    market_words = set(re.findall(r"[a-z]+", m["market"].lower()))
    dfn = m.get("definition") or {}
    dfn_text = " ".join(str(dfn.get(k) or "") for k in ("definition", "measures", "included", "excluded"))
    # drones are a template leftover only when neither the market name nor its definition concerns them
    drone_ok = bool(market_words & {"drone", "drones", "uav", "uas"}) or bool(_DRONE.search(dfn_text))
    leftovers, internal, published = [], [], []
    for b, h1, h2 in _sections(blocks):
        text = b["text"]
        for mm in _LEFTOVER.finditer(text):
            leftovers.append(f"'{mm.group(0)}' in: {text[max(0, mm.start() - 60): mm.end() + 20].strip()}")
        if not drone_ok:
            for mm in _DRONE.finditer(text):
                leftovers.append(f"'{mm.group(0)}' in: {text[max(0, mm.start() - 60): mm.end() + 20].strip()}")
        for mm in _INTERNAL.finditer(text):
            internal.append(f"'{mm.group(0)}' in: {text[max(0, mm.start() - 60): mm.end() + 40].strip()}")
        if not h2.startswith("A.2"):
            for mm in _REGISTER_ID.finditer(text):
                internal.append(f"'{mm.group(0)}' outside A.2 in: {text[max(0, mm.start() - 60): mm.end() + 40].strip()}")
        # the Research Methodology chapter names data triangulation as a step of CMI's process (the VP's text,
        # 2026-10-19); that describes the method, not a comparison with published estimates
        for mm in ([] if re.search(r"Research Methodology", h1) else _PUBLISHED.finditer(text)):
            published.append(text[max(0, mm.start() - 60): mm.end() + 40].strip())
    blockers += [f"leftover in client text: {x[:160]}" for x in dict.fromkeys(leftovers)]
    blockers += [f"internal pipeline wording in client text: {x[:160]}" for x in dict.fromkeys(internal)]

    n_bad = len(qa_report.get("numbers_not_in_model") or [])
    n_inc = len(qa_report.get("same_fact_inconsistencies") or {})
    if n_bad:
        blockers.append(f"{n_bad} text block(s) with a number not in the model (qa_report.numbers_not_in_model)")
    if n_inc:
        blockers.append(f"{n_inc} fact(s) stated with different numbers (qa_report.same_fact_inconsistencies): "
                        + "; ".join(list(qa_report["same_fact_inconsistencies"])[:5]))
    for x in computed.get("math_sums_cagr_increments") or []:
        blockers.append(f"model arithmetic: {x}")

    chk = m.get("size_check")
    if chk and chk.get("status") != "sourced" and published:
        blockers.append(f"headline is unsourced, but the text mentions published estimates or triangulation: "
                        f"'{published[0][:140]}'" + (f" (+{len(published) - 1} more)" if len(published) > 1 else ""))
    reviews = {k: m.get(k) for k in REVIEWS}
    if fact_review is not None and not reviews.get("fact_review"):
        reviews["fact_review"] = fact_review
    for key, rev in reviews.items():
        if not isinstance(rev, dict):
            continue
        blockers += [f"{key}: {x}" for x in rev.get("blockers") or []]
        warnings += [f"{key}: {x}" for x in rev.get("warnings") or []]
    blockers += [f"vendor share > 20% without a source: {x}" for x in unsourced_big_shares(m)]

    blockers += [f"empty heading: '{x}'" for x in empty_heading_blocks(blocks)]
    kc = key_conclusions_count(blocks)
    if kc is None or kc < 3:
        blockers.append(f"'Key conclusions' has {kc or 0} item(s) (at least 3 needed)")
    blockers += [f"country profile: {x}" for x in profiles_without_lead(m, blocks)]

    # user decision 2026-10-16: the AI review is a judgement and varies between runs, so it never blocks;
    # its items are warnings for the human reviewer (high first). Only deterministic checks block.
    if not plausibility or not plausibility.get("ran"):
        warnings.append("AI plausibility review did not run"
                        + (f" ({plausibility.get('error')})" if plausibility and plausibility.get("error") else "")
                        + " -- review the report by hand")
    else:
        for it in plausibility.get("items") or []:
            line = f"{it['category']}: {it['issue']} -- '{it['quote'][:100]}'"
            if it["severity"] in ("high", "medium"):
                warnings.append(f"plausibility review ({it['severity']}) {line}")

    warnings += [f"share claim vs growth: {x}" for x in computed.get("share_claims_vs_growth") or []]
    warnings += [f"announced count: {x}" for x in computed.get("announced_counts") or []]
    warnings += [f"executive summary: {x}" for x in computed.get("exec_summary_numbers_not_in_body") or []]
    if qa_report.get("repeated_sentences"):
        warnings.append(f"{len(qa_report['repeated_sentences'])} repeated sentence(s)")
    if qa_report.get("fact_identity_flags"):
        warnings.append(f"{len(qa_report['fact_identity_flags'])} fact-identity flag(s) (qa_report.fact_identity_flags)")
    return {"ready": not blockers, "blockers": blockers, "warnings": warnings}


# --------------------------------------------------------------------------- #
# internal QA report (markdown; never sent to the client)
# --------------------------------------------------------------------------- #
def _bullets(items, empty="none") -> list[str]:
    items = [str(x) for x in items or [] if str(x).strip()]
    return [f"- {x}" for x in items] if items else [f"- {empty}"]


def _review_md(rev: dict | None, missing: str) -> list[str]:
    if not isinstance(rev, dict):
        return [missing]
    out = ["**Blockers**", *_bullets(rev.get("blockers")), "", "**Warnings**", *_bullets(rev.get("warnings"))]
    rest = {k: v for k, v in rev.items() if k not in ("blockers", "warnings")}
    if rest:
        out += ["", "<details><summary>Full record</summary>", "", "```json",
                json.dumps(rest, indent=1, ensure_ascii=False, default=str)[:20000], "```", "", "</details>"]
    return out


def _pct_delta(raw, final) -> str:
    try:
        raw, final = float(raw), float(final)
    except (TypeError, ValueError):
        return "n/a"
    return f"{(final / raw - 1) * 100:+.1f}%" if raw else "n/a"


def raw_vs_final(m: dict, fleet: dict) -> list[str]:
    """Headline, regions and the top-10 countries: the agents' own answer (fields_raw) vs the report."""
    raw = fleet.get("fields_raw") or fleet.get("fields") or {}
    ms = raw.get("market_size") or {}
    g = m["global"]
    lines = ["| Item | Raw (agent) | Final (report) | Delta |", "|---|---|---|---|",
             f"| Global {model.Y0 - 1} (USD mn) | {ms.get('base_year_value_usd_mn', 'n/a')} | {g['v25']:.1f} | "
             f"{_pct_delta(ms.get('base_year_value_usd_mn'), g['v25'])} |",
             f"| Global CAGR (%) | {ms.get('cagr_pct', 'n/a')} | {g['cagr']:.2f} | "
             + (f"{g['cagr'] - float(ms['cagr_pct']):+.2f} pp |" if isinstance(ms.get("cagr_pct"), (int, float)) else "n/a |")]
    regs = (raw.get("regional") or {}).get("regions") or {}

    def key(n):
        return re.sub(r"\s+", " ", str(n).lower().replace(" and ", " & ")).strip()
    rraw = {key(k): v for k, v in regs.items() if isinstance(v, dict)}
    for r, v in m["regions"].items():
        rv = (rraw.get(key(r)) or {}).get("value_usd_mn")
        lines.append(f"| {r} {model.Y0 - 1} (USD mn) | {rv if rv is not None else 'n/a'} | {v['v25']:.1f} | "
                     f"{_pct_delta(rv, v['v25'])} |")
    craw = {}
    for v in regs.values():
        for c in (v or {}).get("countries") or [] if isinstance(v, dict) else []:
            if isinstance(c, dict) and c.get("name"):
                craw[key(c["name"])] = c.get("value_usd_mn")
    allc = [(c, v, reg) for reg, d in m["countries"].items() for c, v in d.items() if not c.startswith("Rest of")]
    for c, v, reg in sorted(allc, key=lambda x: -x[1]["v26"])[:10]:
        ser = (m.get("country_series") or {}).get(reg, {}).get(c) or {}
        f25 = ser.get(model.Y0 - 1) or ser.get(str(model.Y0 - 1)) or v["v26"] / (1 + v["cagr"] / 100)
        rv = craw.get(key(c))
        lines.append(f"| {c} ({reg}) {model.Y0 - 1} (USD mn) | {rv if rv is not None else 'n/a'} | {float(f25):.1f} | "
                     f"{_pct_delta(rv, f25)} |")
    lines.append("")
    lines.append("Raw = the agents' answers before any normalisation (`fields_raw`); a country's final "
                 f"{model.Y0 - 1} value is back-cast from {model.Y0}E at its CAGR where the model has no "
                 f"{model.Y0 - 1} value.")
    return lines


def write_internal_qa(path: Path, m: dict, fleet: dict, *, gate: dict, qa_report: dict, audit_res: dict,
                      prose: dict | None = None, fact_review: dict | None = None, client_doc: str = "") -> Path:
    """<slug>-internal-qa.md: everything the reviewer needs to decide whether to ship."""
    from datetime import datetime
    internal = qa_report.get("internal_log") or {}
    L = [f"# Internal QA: {m['market']}", "",
         f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}. Internal document -- never send to a client.", "",
         f"Client document: `{client_doc}`", "",
         "## 1. Release gate", "",
         f"**{'READY' if gate['ready'] else 'NOT READY'}** -- {len(gate['blockers'])} blocker(s), "
         f"{len(gate['warnings'])} warning(s).", "", "### Blockers", *_bullets(gate["blockers"]), "",
         "### Warnings", *_bullets(gate["warnings"]), ""]

    chk = m.get("size_check") or {}
    L += ["## 2. Headline market-size check (PLAN-02)", "", f"Status: **{chk.get('status', 'n/a')}**"]
    src = chk.get("source")
    if chk.get("status") == "sourced" and src:
        L += ["", f"- Quote: \"{src.get('quote')}\"", f"- Source: {src.get('publisher')} ({src.get('domain')}) {src.get('url') or ''}",
              f"- Value: {src.get('value_usd_mn')} USD mn in {src.get('year')} ({src.get('derivation')})",
              f"- CAGR sourced: {chk.get('cagr_sourced')}"]
    else:
        L += ["", "Unsourced: the report calls the headline a CMI estimate."]
    L += ["", "Notes:", *_bullets(chk.get("notes")), f"- re-asked: {chk.get('reasked', 'n/a')}", ""]

    geo = m.get("geo_review")
    L += ["## 3. Geography review (PLAN-03)", ""]
    if isinstance(geo, dict):
        reg = geo.get("regional") or {}
        L += [f"Regions: status **{reg.get('status')}**, regional sum {reg.get('sum_usd_mn')} vs global "
              f"{reg.get('global_usd_mn')} (ratio {reg.get('ratio')}), source: {reg.get('source')}, "
              f"re-asked: {reg.get('reasked')}", ""]
        L += ["| Region | Status | Rest share % | Re-asked | First problems | Problems now | Changes |",
              "|---|---|---|---|---|---|---|"]
        for r, c in (geo.get("splits") or {}).items():
            fp = "; ".join(p.get("detail", "") for p in c.get("first_problems") or []) or "-"
            pn = "; ".join(p.get("detail", "") for p in c.get("problems") or []) or "-"
            L.append(f"| {r} | {c.get('status')} | {c.get('rest_share_pct')} | {c.get('reasked')} | {fp[:200]} | "
                     f"{pn[:200]} | {'; '.join(c.get('changes') or [])[:200] or '-'} |")
        L += ["", "Sanity review flags:"]
        L += _bullets([f"{f.get('country')} ({f.get('region')}): {f.get('problem')} -- {f.get('outcome')}"
                       for f in (geo.get("sanity") or {}).get("flags") or []])
        L += ["", "Normalised:", *_bullets(geo.get("normalised")), ""]
        L += ["**Blockers**", *_bullets(geo.get("blockers")), "", "**Warnings**", *_bullets(geo.get("warnings")), ""]
    else:
        L += ["Not available (model predates PLAN-03).", ""]

    L += ["## 4. Segment review (PLAN-04)", "",
          *_review_md(m.get("segment_review"), "Not available (model predates PLAN-04)."), ""]
    L += ["## 5. Vendor review (PLAN-05)", "",
          *_review_md(m.get("vendor_review"), "Not available in this build (PLAN-05 not merged)."), ""]
    L += ["Vendor shares above 20% without a source:", *_bullets(unsourced_big_shares(m)), ""]
    L += ["## 6. Fact checks (PLAN-06)", "",
          *_review_md(m.get("fact_review") or fact_review, "Not available in this build (PLAN-06 not merged)."), ""]

    v = qa_report.get("verification") or {}
    L += ["## 7. Number checks", "",
          f"- numbers checked: {qa_report.get('numbers_checked')}",
          f"- text blocks with a number not in the model: {len(qa_report.get('numbers_not_in_model') or [])}",
          f"- facts stated more than once: {qa_report.get('facts_stated_more_than_once')}; inconsistent: "
          f"{len(qa_report.get('same_fact_inconsistencies') or {})}",
          f"- sweep: first {v.get('first_sweep', 'n/a')}, removed {v.get('fixed', 'n/a')}, final {v.get('final_sweep', 'n/a')}",
          "", "Numbers not in the model:"]
    L += _bullets([f"{i.get('numbers_not_in_model')}: {i.get('text', '')[:160]}"
                   for i in qa_report.get("numbers_not_in_model") or []])
    L += ["", "Inconsistent repeated facts:"]
    L += _bullets([f"{k}: {json.dumps(val, ensure_ascii=False)[:200]}"
                   for k, val in (qa_report.get("same_fact_inconsistencies") or {}).items()])
    L += ["", "Fact-identity flags:", *_bullets(x[:220] for x in qa_report.get("fact_identity_flags") or []), ""]
    L += ["Consistency log:", *_bullets(internal.get("consistency_log")), ""]

    L += ["## 8. Dropped sentences", ""]
    dropped = (prose or {}).get("dropped") or qa_report.get("prose_dropped") or {}
    n = 0
    for slot, xs in dropped.items():
        for x in xs or []:
            n += 1
            L.append(f"- [{slot}] {str(x)[:300]}")
    L += [f"- (none from the prose guards)"] if not n else []
    guard = [x[len("guard dropped: "):] for x in qa_report.get("build_log") or [] if str(x).startswith("guard dropped: ")]
    L += ["", "Removed from agent-written table/box text by the guards:", *_bullets(x[:300] for x in guard),
          "", f"Removed by the final number sweep: {v.get('fixed', 0)} sentence(s).", ""]

    L += ["## 9. Corrections, allocation and build log", "",
          *_bullets(internal.get("corrections") or m.get("corrections")), "", "Allocation rules:",
          *_bullets(m.get("allocation_rules")), "", "Build log:",
          *_bullets(x for x in qa_report.get("build_log") or [] if not str(x).startswith("guard dropped: ")), ""]
    tv = internal.get("to_verify")
    if tv is None:
        tv = [b for b in audit_res.get("_to_verify_doc") or []]
    L += ["## 10. Items marked [to verify]", "", *_bullets(tv), ""]
    L += ["## 11. Raw vs final", "", *raw_vs_final(m, fleet), ""]

    pr = audit_res.get("plausibility_review") or {}
    L += ["## 12. AI plausibility review", ""]
    if not pr.get("ran"):
        L += [f"Did not run{': ' + pr['error'] if pr.get('error') else ''}.", ""]
    else:
        L += ["| Severity | Category | Quote | Issue | Fix |", "|---|---|---|---|---|"]
        order = {"high": 0, "medium": 1, "low": 2}
        for it in sorted(pr.get("items") or [], key=lambda x: order.get(x["severity"], 3)):
            L.append("| " + " | ".join(str(it.get(k, "")).replace("|", "/").replace("\n", " ")[:240]
                                       for k in ("severity", "category", "quote", "issue", "fix")) + " |")
        if not pr.get("items"):
            L.append("| - | - | - | no issue found | - |")
        L.append("")

    comp = audit_res.get("computed") or {}
    L += ["## 13. Other computed checks", ""]
    for k, val in comp.items():
        if isinstance(val, list):
            L += [f"**{k}** ({len(val)})", *_bullets(x[:220] for x in val[:30]), ""]
        else:
            L += [f"**{k}**: `{json.dumps(val, ensure_ascii=False)[:600]}`", ""]
    ai = audit_res.get("ai_review")
    if ai:
        L += ["## 14. AI relevance review", "", "```json", json.dumps(ai, indent=1, ensure_ascii=False)[:15000], "```", ""]
    path.write_text("\n".join(L), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
def client_docx(mdir: Path, slug: str) -> Path:
    """The finished document: the client file, or its DRAFT when the last gate failed."""
    final = mdir / f"{slug}-cmi-whitepaper.docx"
    draft = mdir / f"{slug}-cmi-whitepaper.DRAFT.docx"
    return final if final.exists() or not draft.exists() else draft


def audit(slug: str, use_ai: bool = True, gate: bool = False, market_dir: Path | None = None, *,
          qa_report: dict | None = None, llm=None, m: dict | None = None, ag: dict | None = None,
          fleet: dict | None = None, prose: dict | None = None) -> dict:
    mdir = Path(market_dir) if market_dir else FLEET / "output" / slug
    fleet = fleet or json.loads((mdir / "whitepaper_fields.json").read_text(encoding="utf-8"))
    if m is None or ag is None:
        cmi = json.loads((mdir / "cmi_fields.json").read_text(encoding="utf-8"))
        ag = cmi["fields"]
        m = model.build(fleet, cmi)
    path = client_docx(mdir, slug)
    paras, tables = _doc_texts(path)
    res = {
        "market": m["market"],
        "computed": {
            "math_sums_cagr_increments": check_math(m),
            "share_claims_vs_growth": check_share_claims(m, paras),
            # informational: ties in the data (the claim guard blocks any untied claim about them)
            "ranking_ties_info": check_ties(m),
            "blank_or_dash_cells": check_tables(tables),
            "announced_counts": check_counts(paras, tables, ag),
            "countries_without_profile": check_profiles(m, paras),
            "exec_summary_numbers_not_in_body": check_exec_vs_body(paras),
            "labels": check_labels(paras),
        },
    }
    blocks = doc_blocks(path) if gate or use_ai else []
    if use_ai:
        res["ai_review"] = ai_review(mdir, m, ag, paras, llm)
        res["plausibility_review"] = plausibility_review(m, "\n".join(b["text"] for b in blocks if b["text"].strip()),
                                                         llm)
    if gate:
        if qa_report is None:
            # the number checks re-run on the document as it is now (the factcheck may have changed since)
            from AiAgentsFleet.cmi_report.build import _root
            from AiAgentsFleet.cmi_report.run_cmi_report import qa
            old = json.loads((mdir / "qa_report.json").read_text(encoding="utf-8")) \
                if (mdir / "qa_report.json").exists() else {}
            qa_report = {**old, **qa(path, m, ag, _root(m["market"]))}
        fact_review = None
        if not m.get("fact_review") and (mdir / "fact_checks.json").exists():
            fact_review = json.loads((mdir / "fact_checks.json").read_text(encoding="utf-8"))
        res["gate"] = release_gate(m, blocks, qa_report=qa_report, computed=res["computed"],
                                   plausibility=res.get("plausibility_review"), fact_review=fact_review)
        if prose is None and (mdir / "cmi_prose.json").exists():
            prose = json.loads((mdir / "cmi_prose.json").read_text(encoding="utf-8"))
        res["_to_verify_doc"] = list(dict.fromkeys(
            s[:200] for b in blocks for s in sentences(b["text"]) if "[to verify]" in s))
        res["internal_qa"] = str(write_internal_qa(mdir / f"{slug}-internal-qa.md", m, fleet, gate=res["gate"],
                                                   qa_report=qa_report, audit_res=res, prose=prose,
                                                   fact_review=fact_review, client_doc=path.name))
        res.pop("_to_verify_doc")
    (mdir / "audit_report.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    return res


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--market-slug", required=True)
    p.add_argument("--no-ai", action="store_true", help="skip both LLM reviews (the gate then fails: no plausibility review)")
    p.add_argument("--gate", action="store_true", help="blocking release gate: exit 1 when the report is not ready")
    p.add_argument("--market-dir", help="market folder (default AiAgentsFleet/output/<slug>)")
    a = p.parse_args()
    r = audit(a.market_slug, not a.no_ai, gate=a.gate, market_dir=Path(a.market_dir) if a.market_dir else None)
    if not a.gate:
        print(json.dumps(r, indent=1, ensure_ascii=False))
        return 0
    g = r["gate"]
    print(f"{a.market_slug}: {'READY' if g['ready'] else 'NOT READY'} -- {len(g['blockers'])} blocker(s), "
          f"{len(g['warnings'])} warning(s); internal QA: {r['internal_qa']}")
    for b in g["blockers"]:
        print(f"  BLOCKER {b}")
    return 0 if g["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
