"""Builds a market whitepaper by cloning `vitamin_c_market_whitepaper_v2.docx`
itself -- its cover, table of contents, section headings, confidence boxes,
callout boxes, bullet styles, tables and footer -- and filling every block
with this market's (consistency-reconciled) data. The template's closing
IMPORTANT NOTICE / Disclaimer page is intentionally not reproduced.

All prose is generated from the market's own numbers (no LLM calls), in the
template's sentence patterns.

Usage:
    python build_from_template.py --market-slug <slug>
Writes: AiAgentsFleet/output/<slug>/<slug>-whitepaper.docx
(charts must already exist -- run make_charts.py first; run_report.py does both)
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import docx
from docx.oxml.ns import qn
from PIL import Image, ImageDraw, ImageFont

import labels as dim_labels
from labels import mentions, similar_heading
from narrative import build_narrative, fmt_pct, fmt_usd_mn, ordered_regions

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from AiAgentsFleet.consistency import concentration_label  # noqa: E402
from build_content import _phrase, _snippet_counts, compute_confidence  # noqa: E402

REPORT_DIR = Path(__file__).resolve().parent
FLEET_DIR = REPORT_DIR.parent
TEMPLATE = FLEET_DIR.parent / "vitamin_c_market_whitepaper_v2.docx"
OUTPUT_ROOT = FLEET_DIR / "output"

# Template body-element indices of each reusable building block (see the
# element map of vitamin_c_market_whitepaper_v2.docx).
COVER = range(0, 19)          # cover page, incl. meta table (17) and page break (18)
TOC = range(19, 35)           # "Table of Contents" + 15 numbered entries (last has page break)
SEC_LABEL, SEC_TITLE = 35, 36
CONF_ACCURATE, CONF_MODERATE, CONF_MODERATE_SIZE = 37, 92, 53
SPACER, BODY, CALLOUT = 38, 55, 41
H2, H3, SUBHEAD = 58, 94, 121
BOLD_HEAD, BOLD_BODY = 43, 44
FIGURE, CAPTION, SIDE_FIGURE = 56, 57, 95
BULLET, BULLET_LEAD, THESIS_LEAD = 211, 122, 261
REGION_HEAD, KEYDYN_HEAD, LIST_BULLET = 148, 153, 154
PRICE_TABLE, GTM_TABLE, TABLE_CAPTION = 137, 236, 238
CONCLUDING_HEAD = 265
PAGE_BREAK = 62

TEMPLATE_REGION_ORDER = ["North America", "Europe", "Asia Pacific", "Latin America", "Middle East & Africa"]


# --------------------------------------------------------------------------- #
# small text helpers
# --------------------------------------------------------------------------- #
def _usd(v_mn: float) -> str:
    return fmt_usd_mn(v_mn or 0)


def _usd_title(v_mn: float) -> str:
    """'USD 2.96 Billion' / 'USD 297.2 Million' (template bullet headings)."""
    return _usd(v_mn).replace("billion", "Billion").replace("million", "Million")


def _pct(v: float, d: int = 1) -> str:
    return fmt_pct(v or 0, d)


def _join(items: list[str]) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + (", and " if len(items) > 2 else " and ") + items[-1]


def _sentence(text: str) -> str:
    text = (text or "").strip()
    return text if not text or text[-1] in ".!?" else text + "."


def _score(v: float) -> str:
    """1-5 force score as printed in headings and on the radar chart: 4 -> '4', 2.7 -> '2.7'."""
    return f"{round(float(v or 0), 1):g}"


_RATING_PATTERNS = [
    r"^\s*Rated\s+(?:High|Medium|Low|Moderate)\s+(?:at\s+)?\d(?:\.\d+)?\s*(?:out of|/)\s*5\s*[,:.-]?\s*(?:as\s+|because\s+|since\s+)?",
    r"^\s*(?:High|Medium|Low|Moderate)\s*\(\s*\d(?:\.\d+)?\s*/\s*5\s*\)\s*[:,.-]\s*",
    r"^\s*Rated\s+\d(?:\.\d+)?\s*(?:out of|/)\s*5\s*(?:\([^)]*\))?\s*[,:.-]?\s*(?:as\s+)?",
    r"^[^.:]{0,40}?\bis\s+(?:high|medium|low|moderate)\s*\(\s*\d(?:\.\d+)?\s*/\s*5\s*\)\s*[,:.-]?\s*(?:as\s+|because\s+)?",
]


def _strip_rating(text: str | None) -> str | None:
    """The bullet heading already says e.g. 'High (4/5):' -- remove a rating the
    AI repeated at the start of its rationale ('High (3.8/5): ...',
    'Rated 2.7 out of 5 (Medium), as ...', 'Supplier power is moderate (3/5), as ...')."""
    if not text:
        return text
    for pat in _RATING_PATTERNS:
        text = re.sub(pat, "", text, flags=re.I)
    return text[:1].upper() + text[1:]


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _short(name: str) -> str:
    return str(name or "").split(" (")[0].strip()


def _drop_repeated_lead(lead: str, text: str) -> str:
    """The AI sometimes opens a bullet by repeating its own bold lead
    ('Bear Case – USD 9.20 Billion by 2035 (7.2% CAGR): Bear Case – USD 9.20
    Billion by 2035 (7.17% CAGR): Prolonged ...') -- keep only the body."""
    text = (text or "").strip()
    head = lead.rstrip(": ").strip()
    while ":" in text[: len(head) + 40]:
        before, after = text.split(":", 1)
        if not (similar_heading(before, head) or before.strip().lower() == head.lower()):
            break
        text = after.strip()
    return _cap(text)


def _price(v: float) -> str:
    v = float(v or 0)
    if v == 0:
        return "0"
    if abs(v) < 10:
        return f"{v:,.2f}"
    return f"{v:,.1f}".rstrip("0").rstrip(".") if abs(v) < 100 else f"{v:,.0f}"


# --------------------------------------------------------------------------- #
# docx cloning helpers
# --------------------------------------------------------------------------- #
class Builder:
    def __init__(self, template: Path):
        self.doc = docx.Document(str(template))
        self.body = self.doc.element.body
        self.protos = [copy.deepcopy(el) for el in self.body.iterchildren()]
        self.sect_pr = self.protos[-1]
        self.out: list = []
        self._pic_id = 5000
        self.section = 0
        self._fig = self._tbl = 0
        self.titles: dict[int, str] = {}    # section number -> title (the TOC is synced to these)
        self.toc_entries: list = []
        self.heads: list[tuple[int, str]] = []    # every heading/tagline emitted, for de-dup + QA
        self.captions: list[str] = []
        self.skipped: list[str] = []
        self.texts: list[tuple[int, str]] = []    # (section, text) of everything added, for QA
        self.market = ""                          # market name words don't count when comparing headings

    # -- element access
    def clone(self, idx: int):
        return copy.deepcopy(self.protos[idx])

    def add(self, el):
        self.out.append(el)
        if self.section:
            self.texts.append((self.section, "".join(t.text or "" for t in el.iter(qn("w:t")))))
        return el

    # -- paragraph text
    @staticmethod
    def _runs(p_el):
        return [r for r in p_el.findall(qn("w:r")) if r.find(qn("w:drawing")) is None]

    @staticmethod
    def _set_run_text(r_el, text: str) -> None:
        for t in r_el.findall(qn("w:t")):
            r_el.remove(t)
        for br in r_el.findall(qn("w:br")):
            r_el.remove(br)
        t = r_el.makeelement(qn("w:t"), {})
        t.text = text
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        r_el.append(t)

    def set_text(self, p_el, text: str):
        """Keeps the paragraph's first text run (its formatting) and drops the rest."""
        runs = self._runs(p_el)
        if not runs:
            r = p_el.makeelement(qn("w:r"), {})
            p_el.append(r)
            runs = [r]
        self._set_run_text(runs[0], text)
        for r in runs[1:]:
            p_el.remove(r)
        return p_el

    def set_lead(self, p_el, lead: str, rest: str):
        """Bold lead-in run + normal run (template bullet pattern)."""
        runs = self._runs(p_el)
        self._set_run_text(runs[0], lead)
        if len(runs) > 1:
            self._set_run_text(runs[1], " " + rest)
            for r in runs[2:]:
                p_el.remove(r)
        else:
            r2 = copy.deepcopy(runs[0])
            rpr = r2.find(qn("w:rPr"))
            if rpr is not None and rpr.find(qn("w:b")) is not None:
                rpr.remove(rpr.find(qn("w:b")))
            self._set_run_text(r2, " " + rest)
            p_el.append(r2)
        return p_el

    def set_table_cell_text(self, tbl_el, text: str) -> None:
        p = tbl_el.find(".//" + qn("w:p"))
        self.set_text(p, text)

    # -- images
    def _bind_image(self, el, path: Path, max_cy: int | None = None) -> None:
        r_id, _ = self.doc.part.get_or_add_image(str(path))
        with Image.open(path) as im:
            w, h = im.size
        for blip in el.iter(qn("a:blip")):
            blip.set(qn("r:embed"), r_id)
        for ext_tag in (qn("wp:extent"), qn("a:ext")):
            for ext in el.iter(ext_tag):
                cx = int(ext.get("cx"))
                cy = int(cx * h / w)
                if max_cy and cy > max_cy:      # tall/square chart: shrink, keep aspect
                    cx, cy = int(max_cy * w / h), max_cy
                ext.set("cx", str(cx))
                ext.set("cy", str(cy))
        for tag in (qn("wp:docPr"), qn("pic:cNvPr")):
            for node in el.iter(tag):
                self._pic_id += 1
                node.set("id", str(self._pic_id))

    # -- composite blocks
    def section_start(self, num: int, title: str, confidence: str, first: bool = False) -> None:
        if not first:
            self.add(self.clone(PAGE_BREAK))
        self.section, self._fig, self._tbl = num, 0, 0
        self.titles[num] = title
        self.add(self.set_text(self.clone(SEC_LABEL), f"SECTION {num}"))
        self.add(self.set_text(self.clone(SEC_TITLE), title))
        # DATA CONFIDENCE boxes intentionally omitted (not wanted in these reports).
        self.add(self.clone(SPACER))

    def heading(self, proto: int, text: str) -> None:
        self.heads.append((self.section, text))
        self.add(self.set_text(self.clone(proto), text))

    def para(self, text: str) -> None:
        self.add(self.set_text(self.clone(BODY), text))

    def callout(self, text: str) -> None:
        """Callouts are taglines; one that repeats an earlier heading or
        tagline adds nothing, so it is left out rather than printed twice."""
        if any(similar_heading(text, h, self.market) for _, h in self.heads):
            self.skipped.append(f"Section {self.section} callout (repeats an earlier heading): {text[:70]}")
            return
        self.heads.append((self.section, text))
        tbl = self.clone(CALLOUT)
        self.set_table_cell_text(tbl, text)
        self.add(tbl)
        self.add(self.clone(SPACER))

    def h2(self, text: str) -> None:
        self.heading(H2, text)

    def h3(self, text: str) -> None:
        self.heading(H3, text)

    def subhead(self, text: str) -> None:
        self.heading(SUBHEAD, text)

    def finding(self, head: str, text: str) -> None:
        self.heading(BOLD_HEAD, head)
        self.add(self.set_text(self.clone(BOLD_BODY), _drop_repeated_lead(head, text)))

    def _caption(self, kind: str, caption: str) -> str:
        """Numbers figures/tables by the section they sit in (Figure 9.2 = the
        second figure of Section 9)."""
        if kind == "Figure":
            self._fig += 1
            n = self._fig
        else:
            self._tbl += 1
            n = self._tbl
        text = f"{kind} {self.section}.{n} - {caption}"
        self.captions.append(text)
        return text

    def figure(self, path: Path, caption: str) -> None:
        if not path.exists():
            return
        el = self.clone(FIGURE)
        self._bind_image(el, path, max_cy=3_000_000)   # ~8.3 cm, the template's figure height
        self.add(el)
        self.add(self.set_text(self.clone(CAPTION), self._caption("Figure", caption)))

    def table_caption(self, caption: str) -> None:
        self.add(self.set_text(self.clone(TABLE_CAPTION), self._caption("Table", caption)))

    def side_figure(self, path: Path, caption: str, text: str) -> None:
        """Template Section 5 pattern: a floating donut beside the paragraph,
        with the figure caption drawn under the image itself."""
        if not path.exists():
            self.para(text)
            return
        captioned = path.with_name(path.stem + "_captioned.png")
        _caption_image(path, captioned, self._caption("Figure", caption))
        el = self.clone(SIDE_FIGURE)
        self._bind_image(el, captioned)
        runs = self._runs(el)
        self._set_run_text(runs[0], text)
        for r in runs[1:]:
            el.remove(r)
        self.add(el)

    def bullets(self, items: list[str], proto: int = BULLET) -> None:
        for it in items:
            self.add(self.set_text(self.clone(proto), it))

    def lead_bullets(self, items: list[tuple[str, str]], proto: int = BULLET_LEAD) -> None:
        for lead, rest in items:
            self.heads.append((self.section, lead))
            self.add(self.set_lead(self.clone(proto), lead, _drop_repeated_lead(lead, rest)))

    def sync_toc(self) -> None:
        """TOC entries carry the template's Vitamin C section names; replace
        them with the titles actually used in this report."""
        for num, el in enumerate(self.toc_entries, start=1):
            title = self.titles.get(num)
            if not title:
                continue
            runs = self._runs(el)
            self._set_run_text(runs[0], title.title() if title.isupper() else title)
            for r in runs[1:]:              # keep the runs (one holds the page break), drop their text
                for t in r.findall(qn("w:t")):
                    r.remove(t)

    def table(self, proto: int, rows: list[list[str]]) -> None:
        tbl = self.clone(proto)
        trs = tbl.findall(qn("w:tr"))
        header, row_proto = trs[0], trs[1]
        for tr in trs[1:]:
            tbl.remove(tr)
        for row in rows:
            tr = copy.deepcopy(row_proto)
            for tc, val in zip(tr.findall(qn("w:tc")), row):
                self.set_text(tc.find(qn("w:p")), str(val))
            tbl.append(tr)
        if proto == PRICE_TABLE and len(rows) <= 8:   # small table: keep it on one page, like the template
            for p_el in tbl.iter(qn("w:p")):
                ppr = p_el.find(qn("w:pPr"))
                if ppr is None:
                    ppr = p_el.makeelement(qn("w:pPr"), {})
                    p_el.insert(0, ppr)
                if ppr.find(qn("w:keepNext")) is None:
                    ppr.insert(0, ppr.makeelement(qn("w:keepNext"), {}))
        self.add(tbl)

    # -- output
    def save(self, path: Path) -> None:
        for child in list(self.body.iterchildren()):
            self.body.remove(child)
        for el in self.out:
            self.body.append(el)
        self.body.append(self.sect_pr)
        _drop_unused_images(self.doc)
        self.doc.save(str(path))


def _caption_image(src: Path, dst: Path, caption: str) -> None:
    im = Image.open(src).convert("RGB")
    w, h = im.size
    try:
        font = ImageFont.truetype("cambriai.ttf", max(14, w // 34))
    except OSError:
        font = ImageFont.load_default()
    draw = ImageDraw.Draw(im)
    words, lines, line = caption.split(), [], ""
    for wd in words:
        trial = (line + " " + wd).strip()
        if draw.textlength(trial, font=font) > w * 0.95 and line:
            lines.append(line)
            line = wd
        else:
            line = trial
    lines.append(line)
    lh = int(font.size * 1.35)
    out = Image.new("RGB", (w, h + lh * len(lines) + 10), "white")
    out.paste(im, (0, 0))
    d2 = ImageDraw.Draw(out)
    for i, ln in enumerate(lines):
        tw = d2.textlength(ln, font=font)
        d2.text(((w - tw) / 2, h + 4 + i * lh), ln, fill=(75, 85, 99), font=font)
    out.save(dst)


def _drop_unused_images(doc) -> None:
    xml = doc.element.xml
    part = doc.part
    for r_id, rel in list(part.rels.items()):
        if "image" in rel.reltype and f'"{r_id}"' not in xml:
            del part.rels[r_id]


# --------------------------------------------------------------------------- #
# content
# --------------------------------------------------------------------------- #
_FORCE_NOTES = {
    "supplier_power": {
        "High": "a limited pool of input and technology suppliers holds meaningful pricing leverage.",
        "Medium": "suppliers have some pricing leverage, but alternative sources limit their power.",
        "Low": "inputs are widely available, so suppliers have little pricing leverage.",
    },
    "competitive_rivalry": {
        "High": "intense competition among established players keeps pricing and margins under pressure.",
        "Medium": "players compete actively, but differentiation limits pure price competition.",
        "Low": "a small number of players and clear differentiation keep rivalry contained.",
    },
    "buyer_power": {
        "High": "large buyers can negotiate hard on price and switch suppliers with relative ease.",
        "Medium": "large buyers have purchasing leverage, while smaller buyers remain price-takers.",
        "Low": "fragmented buyers have limited ability to negotiate on price.",
    },
    "threat_of_new_entrants": {
        "High": "low barriers to entry allow new competitors to enter quickly.",
        "Medium": "capital and regulatory requirements moderate, but do not prevent, new entry.",
        "Low": "high capital requirements and regulatory barriers limit new competition.",
    },
    "threat_of_substitutes": {
        "High": "readily available alternatives can replace the product in many applications.",
        "Medium": "alternatives exist for some applications, but not as a full replacement.",
        "Low": "alternatives offer limited replacement potential.",
    },
}
_FORCE_NAMES = {
    "supplier_power": "Supplier Power", "competitive_rivalry": "Competitive Rivalry",
    "buyer_power": "Buyer Power", "threat_of_new_entrants": "Threat of New Entrants",
    "threat_of_substitutes": "Threat of Substitutes",
}


def build(slug: str) -> Path:
    market_dir = OUTPUT_ROOT / slug
    data = json.loads((market_dir / "whitepaper_fields.json").read_text(encoding="utf-8"))
    n = build_narrative(data)
    f = n["fields"]
    charts = market_dir / "charts"
    root, by, fy = n["root"], n["base_year"], n["forecast_year"]
    cagr = n["cagr"]
    changes = data.get("consistency_changes", {}) or {}
    snippets = _snippet_counts(data.get("log") or [])

    def conf(key: str, size: bool = False) -> int:
        label, _ = compute_confidence(f.get(key), changes.get(key), snippets.get(key))
        if label == "HIGH":
            return CONF_ACCURATE
        return CONF_MODERATE_SIZE if size else CONF_MODERATE

    # Optional AI-written prose (prose_writer.py). Every slot falls back to
    # the data-only sentence when the AI text is missing or failed the
    # number guard, or when the market's numbers changed since it was written.
    prose_path = market_dir / "prose.json"
    prose = {}
    if prose_path.exists():
        pj = json.loads(prose_path.read_text(encoding="utf-8"))
        from prose_writer import is_current
        if is_current(pj, data["fields"]):
            prose = pj.get("sections", {})
        else:
            print("prose.json is stale (fields or prose rules changed) -- using data-only text; "
                  "re-run prose_writer.py to refresh it")

    # What each segment list actually measures ("Shrimp Species", not "Forms").
    lab = dim_labels.load(market_dir, f, data.get("market_name", ""))
    grade_s, grade_p = lab["grade"]["singular"], lab["grade"]["plural"]
    form_s, form_p = lab["form"]["singular"], lab["form"]["plural"]
    app_s, app_p = lab["application"]["singular"], lab["application"]["plural"]
    grp_p = lab["sub_segment_group"]["plural"]
    mix_s = lab["region_mix"]["singular"]
    low = dim_labels.lower

    def P(sec: int, key: str):
        v = (prose.get(str(sec)) or {}).get(key)
        return v if isinstance(v, str) and v.strip() else None

    def PL(sec: int, key: str):
        v = (prose.get(str(sec)) or {}).get(key)
        if not isinstance(v, list):
            return None
        out = []
        for x in v:
            if isinstance(x, dict):      # the AI sometimes returns {"heading": ..., "text": ...} bullets
                head = next((x[k] for k in ("heading", "head", "lead") if isinstance(x.get(k), str)), "")
                x = f"{head.strip().rstrip(':.')}: {x['text'].strip()}" if head and isinstance(x.get("text"), str) else x.get("text")
            if isinstance(x, str) and x.strip():
                out.append(x.strip())
        return out or None

    def LB(sec: int, key: str, head: str = "lead"):
        v = (prose.get(str(sec)) or {}).get(key)
        if not isinstance(v, list):
            return None
        out = [(x[head].strip(), x["text"].strip()) for x in v
               if isinstance(x, dict) and isinstance(x.get(head), str) and isinstance(x.get("text"), str)]
        out = [(h if h.endswith(":") or head == "head" else h + ":", t) for h, t in out]
        return out or None

    def PD(sec: int, key: str, sub: str):
        v = ((prose.get(str(sec)) or {}).get(key) or {})
        v = v.get(sub) if isinstance(v, dict) else None
        if isinstance(v, str):
            # the heading already carries "High (4/5):" -- drop a repeated rating prefix
            v = re.sub(r"^\s*(High|Medium|Low|Moderate)[^:]{0,20}:\s*", "", v)
        return v

    b = Builder(TEMPLATE)
    b.market = n["market"]
    month = datetime.now().strftime("%B %Y")
    bn = n["base_val"] >= 1000
    unit_word = "billion" if bn else "million"

    # ---- data used across sections
    regions = f.get("regional", {}).get("regions", {}) or {}
    reg_sorted = sorted(regions.items(), key=lambda kv: kv[1].get("value_usd_mn", 0), reverse=True)
    leader, leader_d = reg_sorted[0] if reg_sorted else ("n/a", {})
    fastest, fastest_d = max(regions.items(), key=lambda kv: kv[1].get("cagr_2025_2030_pct", 0)) if regions else ("n/a", {})
    apps = sorted(f.get("end_use_industry", {}).get("applications", []) or [],
                  key=lambda a: a.get("share_pct_of_market", 0), reverse=True)
    fast_app = max(apps, key=lambda a: a.get("cagr_2025_2030_pct", 0)) if apps else {}
    slow_app = min(apps, key=lambda a: a.get("cagr_2025_2030_pct", 0)) if apps else {}
    drivers = sorted(f.get("drivers_restraints", {}).get("drivers", []) or [],
                     key=lambda d: d.get("cagr_contribution_bps", 0), reverse=True)
    restraints = sorted(f.get("drivers_restraints", {}).get("restraints", []) or [],
                        key=lambda r: r.get("cagr_drag_bps", 0), reverse=True)
    dn = [_phrase(d["name"]) for d in drivers]
    rn = [_phrase(r["name"]) for r in restraints]
    companies = f.get("competitive_landscape", {}).get("companies", []) or []
    top3 = sum(c.get("share_pct", 0) for c in companies[:3])
    conc = concentration_label(top3)

    # ===================================================================== #
    # Cover + table of contents (template blocks, market text swapped in)
    # ===================================================================== #
    for i in COVER:
        el = b.clone(i)
        if i == 5:
            b.set_text(el, f"Global {root} Market")
        elif i == 7:
            size = f"${n['base_val'] / 1000:,.2f} billion" if bn else f"${n['base_val']:,.0f} million"
            article = "an" if size[1:].startswith(("8", "11", "18")) else "a"
            b.set_text(el, f"How {article} {size} industry will reshape itself by {fy}")
        elif i == 17:
            cells = el.findall(".//" + qn("w:tc"))
            values = {1: month, 3: "Indicative Estimate  -  Market Intelligence Report",
                      5: "Approximately 20", 7: f"{by} through {fy}"}
            for ci, val in values.items():
                if ci < len(cells):
                    b.set_text(cells[ci].find(qn("w:p")), val)
        b.add(el)
    for i in TOC:
        el = b.add(b.clone(i))
        if i != TOC[0]:
            b.toc_entries.append(el)     # text set by b.sync_toc() once the section titles are known

    # ===================================================================== #
    # 1. Executive summary
    # ===================================================================== #
    b.section_start(1, "EXECUTIVE SUMMARY", conf("market_size"), first=True)
    if cagr < 4:
        tone = "remains a mature, steadily growing segment"
    elif cagr < 8:
        tone = "is a steadily expanding segment"
    elif cagr < 15:
        tone = "is a fast-growing segment"
    else:
        tone = "is an early-stage, high-growth segment"
    b.para(P(1, "p1") or f"The global {root} market {tone}, with demand spread across "
           f"{_join([_short(a['name']) for a in apps[:3]])}"
           f"{' and other ' + low(app_p) if len(apps) > 3 else ''}. We estimate the market at {n['base_val_fmt']} in {by}, "
           f"projected to reach {n['fc_val_fmt']} by {fy}, creating {n['incremental_fmt']} in incremental opportunity.")
    b.para(P(1, "p2") or f"Long-term growth is expected to be driven by {_join(dn[:3])}. At the same time, "
           f"{_join(rn[:2])} will remain key factors shaping pricing, margins, and competitive dynamics across the industry.")
    b.callout(P(1, "callout") or f"{leader} accounts for {_pct(leader_d.get('share_pct_of_global'))} of global {root} market value, "
              f"making regional concentration a defining feature of the market.")
    b.h2("Three Key Findings Shaping Our Market View")
    second = reg_sorted[1] if len(reg_sorted) > 1 else None
    fnd = LB(1, "findings", "head")
    if fnd and len(fnd) >= 3:
        for head, text in fnd[:3]:
            b.finding(head, text)
    else:
        b.finding(f"{leader} leads global demand",
                  f"{leader} represents {_usd(leader_d.get('value_usd_mn'))} in {by}, or "
                  f"{_pct(leader_d.get('share_pct_of_global'))} of global value, and is projected to grow at "
                  f"{_pct(leader_d.get('cagr_2025_2030_pct'))} a year through 2030"
                  + (f", ahead of {second[0]} at {_pct(second[1].get('share_pct_of_global'))} of global value." if second else "."))
        if fast_app and fast_app is apps[0]:
            b.finding(f"{_short(fast_app['name'])} is both the largest and fastest-growing {low(app_s)}",
                      f"{_short(fast_app['name'])} accounts for {_pct(fast_app.get('share_pct_of_market'))} of market value "
                      f"and is expanding at {_pct(fast_app.get('cagr_2025_2030_pct'))} annually, compared with "
                      f"{_pct(cagr)} for the market overall.")
        elif fast_app:
            b.finding(f"{_short(fast_app['name'])} is the fastest-growing {low(app_s)}",
                      f"{_short(fast_app['name'])} is expanding at {_pct(fast_app.get('cagr_2025_2030_pct'))} annually, "
                      f"compared with {_pct(cagr)} for the market overall, while {_short(apps[0]['name'])} remains the "
                      f"largest {low(app_s)} at {_pct(apps[0].get('share_pct_of_market'))} of market value.")
        if companies:
            b.finding(f"The competitive landscape is {conc}",
                      f"{_join([c['name'] for c in companies[:3]])} together account for approximately "
                      f"{_pct(top3)} of global revenue, with {companies[0]['name']} the largest named player at "
                      f"~{companies[0].get('share_pct', 0):.0f}% share.")

    # ===================================================================== #
    # 2. Market size outlook
    # ===================================================================== #
    ms = f.get("market_size", {}) or {}
    # history is printed only when consistency's history_check passed
    hist = (ms.get("historical_usd_mn") or {}) if (ms.get("history_check") or {}).get("ok") else {}
    b.section_start(2, f"Global {root} Market Size Outlook ({by}–{fy})", conf("market_size", size=True))
    hist_txt = ""
    if hist:
        hy = sorted(hist)
        h0, h1 = hist[hy[0]], hist[hy[-1]]
        dips = [y2 for y1, y2 in zip(hy, hy[1:]) if hist[y2] < hist[y1]]
        hist_txt = (f" The market expanded from {_usd(h0)} in {hy[0]} to {_usd(h1)} in {hy[-1]}"
                    + (f", despite a temporary decline in {_join(dips)}." if dips else
                       f", a compound annual growth rate of {_pct(((h1 / h0) ** (1 / max(1, len(hy) - 1)) - 1) * 100)}."))
    b.para(P(2, "p1") or f"We estimate the global {root} market at {n['base_val_fmt']} in {by}.{hist_txt}")
    b.figure(charts / "fig1_1_market_size.png",
             f"Global {root} market: " + (f"historical {sorted(hist)[0]} to {sorted(hist)[-1]} and " if hist else "")
             + f"base-case forecast {by} to {fy}, in USD {unit_word}.")
    b.h2("Long-Term Growth Outlook")
    leader_incr = (leader_d.get("forecast_value_usd_mn", 0) or 0) - (leader_d.get("value_usd_mn", 0) or 0)
    b.para(P(2, "p2") or f"The global {root} market is projected to reach {n['fc_val_fmt']} by {fy}, growing at a CAGR of "
           f"{_pct(cagr)}. {leader} is expected to add the most absolute value, about {_usd(leader_incr)} between "
           f"{by} and 2030, while {_short(fast_app.get('name', ''))} is the fastest-growing {low(app_s)} at "
           f"{_pct(fast_app.get('cagr_2025_2030_pct'))} a year.")
    mult = (n["fc_val"] / n["base_val"]) if n["base_val"] else 0
    b.callout(P(2, "callout") or f"Compounding at {_pct(cagr)} a year, the market grows {mult:.1f}x between {by} and {fy} -- "
              f"long-term value comes from steady compounded growth and strong positioning across the value chain.")

    # ===================================================================== #
    # 3. Drivers & challenges
    # ===================================================================== #
    b.section_start(3, "Market Dynamics – Drivers & Challenges", conf("drivers_restraints"))
    D, R = n["total_driver_bps"], n["total_restraint_bps"]
    balance = "remains strongly favorable" if R < D * 0.35 else "remains favorable" if R < D * 0.6 else "is finely balanced"
    b.para(P(3, "p1") or f"The market's growth fundamentals remain {'strongly positive' if R < D * 0.35 else 'positive'}. "
           f"We estimate that long-term growth drivers contribute nearly {D:.0f} basis points to the market CAGR, "
           f"while structural restraints reduce growth by around {R:.0f} basis points. The overall balance {balance}, "
           f"supporting our projected {_pct(cagr)} long-term growth outlook.")
    b.figure(charts / "fig2_1_drivers_restraints.png",
             "What pushes growth up, and what pulls it down, in basis points of annual CAGR.")
    if drivers:
        b.h2("What Is Driving Market Growth")
        txt = (f"{_cap(dn[0])} remains the market's strongest growth driver, contributing nearly "
               f"{drivers[0].get('cagr_contribution_bps', 0):.0f} basis points to annual growth.")
        if len(drivers) > 2:
            txt += (f" {_cap(dn[1])} ({drivers[1].get('cagr_contribution_bps', 0):.0f} bps) and {dn[2]} "
                    f"({drivers[2].get('cagr_contribution_bps', 0):.0f} bps) are the next most important catalysts.")
        if len(drivers) > 3:
            txt += f" {_cap(_join(dn[3:6]))} continue to strengthen long-term demand."
        b.para(P(3, "drivers") or txt)
    if restraints:
        b.h2("What Continues to Challenge Growth")
        txt = (f"{_cap(rn[0])} remains the market's largest structural risk, contributing nearly "
               f"{restraints[0].get('cagr_drag_bps', 0):.0f} basis points of annual growth drag.")
        if len(restraints) > 1:
            txt += f" {_cap(_join(rn[1:4]))} remain additional long-term headwinds."
        b.para(P(3, "restraints") or txt)

    # ===================================================================== #
    # 4. Porter's five forces
    # ===================================================================== #
    pf = f.get("porters_five_forces", {}) or {}
    forces = pf.get("forces", {}) or {}
    overall = pf.get("overall_score_out_of_5", 3)
    attract = "limited" if overall >= 3.5 else "moderate" if overall >= 2.5 else "high"
    ranked = sorted(((forces[k].get("score_1_5", 3), k) for k in _FORCE_NAMES if k in forces), reverse=True)
    b.section_start(4, "Industry Attractiveness Assessment – Porter’s Five Forces", conf("porters_five_forces"))
    txt = (f"Based on Porter's Five Forces analysis, the global {root} market demonstrates {attract} industry "
           f"attractiveness, with an overall competitive-pressure score of {overall:.1f} out of 5.")
    if ranked and len({s for s, _ in ranked}) > 1:
        txt += (f" {_FORCE_NAMES[ranked[0][1]]} and {_FORCE_NAMES[ranked[1][1]].lower()} remain the most intense "
                f"forces, while {_FORCE_NAMES[ranked[-1][1]].lower()} remains relatively limited.")
    b.para(P(4, "p1") or txt)
    b.figure(charts / "fig3_1_porters.png", f"Porter's Five Forces for the global {root} industry, scored 1 to 5.")
    b.h2(f"How Attractive Is the {root} Industry")
    b.bullets([f"{_FORCE_NAMES[k]} – {forces[k].get('label', 'Medium')} ({_score(forces[k].get('score_1_5', 3))}/5): "
               f"{_cap(_strip_rating(PD(4, 'forces', k)) or _FORCE_NOTES[k][forces[k].get('label', 'Medium')])}"
               for k in ["supplier_power", "competitive_rivalry", "buyer_power", "threat_of_new_entrants", "threat_of_substitutes"]
               if k in forces], proto=BULLET)

    # ===================================================================== #
    # 5. Segmentation (the template's "grades and forms" -- headings use what
    #    the two lists actually are for this market, e.g. "Shrimp Species")
    # ===================================================================== #
    seg = f.get("segmentation_grade_form", {}) or {}
    grades = sorted(seg.get("by_grade_or_type", []) or [], key=lambda g: g.get("share_pct_of_market", 0), reverse=True)
    forms = sorted(seg.get("by_form", []) or [], key=lambda g: g.get("share_pct_of_market", 0), reverse=True)
    if grades and lab["grade_duplicates_application"] and apps:
        # The "grade" list is Section 6's application list again, with different
        # shares -- printing both would show two conflicting splits of one thing.
        b.skipped.append(f"Section 5 {low(grade_p)} split (duplicates Section 6's {low(app_p)} split)")
        grades = []
    seg_title = " and ".join(x for x, items in ((grade_p, grades), (form_p, forms)) if items) or "Product Segments"
    b.section_start(5, f"Understanding Demand Across {seg_title}", conf("segmentation_grade_form"))
    if grades:
        b.h3(f"How Different {grade_p} Shape Market Demand")
        g0 = grades[0]
        txt = (f"The {root} market is segmented across {len(grades)} {low(grade_p)} -- "
               f"{_join([_short(g['name']) for g in grades])} -- each with distinct specifications, requirements, "
               f"and pricing dynamics. {_short(g0['name'])} remains the largest segment, accounting for "
               f"{_pct(g0.get('share_pct_of_market'))} of the {by} market value ({_usd(g0.get('value_usd_mn'))})")
        if len(grades) > 1:
            g1 = grades[1]
            txt += f", followed by {_short(g1['name'])} at {_pct(g1.get('share_pct_of_market'))} ({_usd(g1.get('value_usd_mn'))})"
        txt += "."
        if len(grades) > 2:
            txt += (f" {_short(grades[-1]['name'])} represents the smallest share at "
                    f"{_pct(grades[-1].get('share_pct_of_market'))}.")
        b.side_figure(charts / "fig4_1_grade.png", f"{root} market value by {low(grade_s)}, {by} estimate.",
                      P(5, "grades") or txt)
    if forms:
        b.h3(f"{form_p} and Their Share of Demand")
        f0 = forms[0]
        ff = max(forms, key=lambda x: x.get("fastest_growing_cagr_pct") or 0)
        txt = (f"Demand also varies by {low(form_s)}, which shapes suitability for different uses and pricing "
               f"dynamics. {_short(f0['name'])} dominates, accounting for "
               f"{_pct(f0.get('share_pct_of_market'))} of total value.")
        if ff.get("fastest_growing_cagr_pct"):
            if ff is f0:
                txt += f" It is also the fastest-growing {low(form_s)}, expanding at {_pct(ff.get('fastest_growing_cagr_pct'))} annually."
            else:
                txt += (f" {_short(ff['name'])} is the fastest-growing {low(form_s)}, expanding at "
                        f"{_pct(ff.get('fastest_growing_cagr_pct'))} annually.")
        rest = [x for x in forms[1:] if x is not ff]
        if rest:
            txt += " " + _cap(_join([f"{_short(x['name'])} ({_pct(x.get('share_pct_of_market'))})" for x in rest])) + \
                   " make up the remainder of the market."
        b.side_figure(charts / "fig4_2_form.png", f"{root} market value by {low(form_s)}, {by} estimate.",
                      P(5, "forms") or txt)

    # ===================================================================== #
    # 6. Applications / end uses
    # ===================================================================== #
    b.section_start(6, f"Demand Distribution Across Key {app_p}", conf("end_use_industry"))
    if apps:
        # The two figures below are a share chart and a growth chart -- the
        # heading names exactly that instead of asserting a trend.
        b.h2(f"{app_p}: Share of Demand and Growth Outlook")
        txt = (f"{_short(apps[0]['name'])} remains the largest {root} {low(app_s)} segment, accounting for "
               f"{_pct(apps[0].get('share_pct_of_market'))} of market value")
        if len(apps) > 2:
            txt += (f", followed by {_short(apps[1]['name'])} ({_pct(apps[1].get('share_pct_of_market'))}) and "
                    f"{_short(apps[2]['name'])} ({_pct(apps[2].get('share_pct_of_market'))})")
        txt += "."
        if len(apps) > 3:
            txt += f" {_cap(_join([_short(a['name']) for a in apps[3:]]))} make up the remainder of the demand landscape."
        b.para(P(6, "p1") or txt)
        b.figure(charts / "fig5_1_end_use_share.png", f"Share of market value by {low(app_s)}, {by} estimate.")
        diff = (fast_app.get("cagr_2025_2030_pct", 0) or 0) - cagr
        vs = (f" -- well above the overall market CAGR of {_pct(cagr)}" if diff >= 2 else
              f" -- above the overall market CAGR of {_pct(cagr)}" if diff > 0.2 else
              f", in line with the overall market CAGR of {_pct(cagr)}")
        b.para(P(6, "p2") or f"{_short(fast_app['name'])} {'is also' if fast_app is apps[0] else 'is'} the fastest-growing {root} {low(app_s)}, expanding at "
               f"{_pct(fast_app.get('cagr_2025_2030_pct'))} annually{vs}. "
               + (f"{_short(slow_app['name'])} grows the slowest, at {_pct(slow_app.get('cagr_2025_2030_pct'))} a year."
                  if slow_app is not fast_app else ""))
        b.figure(charts / "fig5_2_end_use_cagr.png", f"Compound annual growth rate by {low(app_s)}, {by} to 2030.")

    # ===================================================================== #
    # 7. Sub-segments
    # ===================================================================== #
    groups = f.get("sub_segments", {}).get("by_top_application", []) or []
    b.section_start(7, "High-Growth Sub-Segments Reshaping the Market", conf("sub_segments"))
    subs_all = [s for g in groups for s in g.get("sub_segments", []) or []]
    above = sum(1 for s in subs_all if (s.get("cagr_pct") or 0) > cagr)
    # Only call the groups "applications" when they are Section 6's applications.
    within = f"major {low(app_s)}" if lab["sub_groups_are_applications"] else f"of the {len(groups)} {low(grp_p)} below"
    if subs_all and above >= len(subs_all) / 2:
        b.para(P(7, "p1") or f"Overall market averages do not fully capture where growth is truly concentrated. Within each "
               f"{within}, select high-growth niches are expanding well above the {_pct(cagr)} market average, offering "
               f"stronger pricing power, faster demand acceleration, and more attractive long-term investment opportunities.")
    else:
        b.para(P(7, "p1") or f"Overall market averages do not fully capture where growth is concentrated. Within each "
               f"{within}, growth rates vary widely: only {above} of the {len(subs_all)} sub-segments we "
               f"track are expanding faster than the {_pct(cagr)} market average, so niche selection matters.")
    b.figure(charts / "fig6_1_sub_segments.png",
             f"Sub-segment growth rates (and sizes, where available) within "
             f"{_join([_short(g.get('application', '')) for g in groups[:3]])}.")
    if groups:
        b.subhead("High-Growth Sub-Segments Driving Future Market Expansion")
        items = []
        for g in groups[:3]:
            subs = sorted(g.get("sub_segments") or [], key=lambda s: s.get("cagr_pct", 0), reverse=True)
            if not subs:
                continue
            top, bottom = subs[0], subs[-1]
            rest = f"{_short(top['name'])} is growing at {_pct(top.get('cagr_pct'))} annually"
            if bottom is not top:
                rest += f", compared with {_pct(bottom.get('cagr_pct'))} for {_short(bottom['name'])}"
            rest += (f" -- {'above' if top.get('cagr_pct', 0) > cagr else 'below'} the {_pct(cagr)} market average.")
            items.append((f"{_short(g.get('application', ''))}:", rest))
        b.lead_bullets(LB(7, "bullets") or items)

    # ===================================================================== #
    # 8. Derivatives & pricing
    # ===================================================================== #
    dp = f.get("derivatives_pricing", {}) or {}
    tiers = sorted(dp.get("price_tiers", []) or [], key=lambda t: t.get("price_low_usd_per_unit", 0))
    derivs = sorted(dp.get("derivatives", []) or [], key=lambda d: d.get("value_usd_mn", 0), reverse=True)
    units = {t.get("unit") for t in tiers if t.get("unit")}
    unit = next(iter(units)) if len(units) == 1 else ""
    b.section_start(8, "Derivatives & Value Across the Pricing Structure", conf("derivatives_pricing"))
    if tiers:
        lo_t, hi_t = tiers[0], tiers[-1]
        b.para(P(8, "p1") or f"The {root} market highlights significant value expansion across the pricing structure. "
               f"{_short(lo_t.get('tier'))} pricing starts at around {_price(lo_t.get('price_low_usd_per_unit'))} "
               f"{unit or 'USD per unit'}, while {_short(hi_t.get('tier'))} pricing can exceed "
               f"{_price(hi_t.get('price_high_usd_per_unit'))} {unit or 'USD per unit'}.")
    if derivs:
        total = sum(d.get("value_usd_mn", 0) or 0 for d in derivs) or 1
        d0 = derivs[0]
        txt = (f"{_short(d0['name'])} accounts for {_usd(d0.get('value_usd_mn'))} or "
               f"{_pct(d0.get('value_usd_mn', 0) / total * 100)} of derivative value")
        if len(derivs) > 1:
            txt += f", while {_join([_short(d['name']) for d in derivs[1:4]])} continue to add value across the chain"
        b.para(P(8, "p2") or txt + ".")
    if tiers:
        lo_mid = (tiers[0].get("price_low_usd_per_unit", 0) + tiers[0].get("price_high_usd_per_unit", 0)) / 2
        hi_mid = (tiers[-1].get("price_low_usd_per_unit", 0) + tiers[-1].get("price_high_usd_per_unit", 0)) / 2
        if lo_mid > 0 and 1.5 <= hi_mid / lo_mid <= 50:   # beyond ~50x the tiers aren't like-for-like
            b.callout(P(8, "callout") or f"Top-tier pricing runs at roughly {hi_mid / lo_mid:.0f}x the entry tier -- the same core "
                      f"product earns materially more value through specification, quality, and positioning.")
    if derivs:
        b.figure(charts / "fig7_1_derivatives.png", f"Derivative product market sizes, {by} estimate.")
    if tiers:
        b.h2("The Multi-Tier Pricing Structure")
        b.para(P(8, "pricing") or f"{root} pricing varies significantly across tiers, reflecting differences in specification, quality "
               f"and certification standards, and end-use positioning. While entry tiers compete on cost, premium "
               f"tiers command significantly higher pricing through value-added features and differentiation.")
        price_col = f"Price ({unit})" if unit else "Price (USD)"
        tbl_rows = [[t.get("tier", ""), f"{_price(t.get('price_low_usd_per_unit'))}-{_price(t.get('price_high_usd_per_unit'))}",
                     t.get("grade", ""), t.get("principal_buyers", "")] for t in tiers]
        b.table(PRICE_TABLE, tbl_rows)
        # header text (column 2 carries the unit)
        hdr = b.out[-1].findall(qn("w:tr"))[0].findall(qn("w:tc"))
        if len(hdr) > 1:
            b.set_text(hdr[1].find(qn("w:p")), price_col)

    # ===================================================================== #
    # 9. Regional outlook
    # ===================================================================== #
    b.section_start(9, "Regional Outlook & Dynamics", conf("regional"))
    smallest = reg_sorted[-1][0] if reg_sorted else ""
    tag = (f"{leader} leads and grows fastest." if leader == fastest else f"{leader} leads. {fastest} grows fastest.")
    if smallest and smallest not in (leader, fastest):
        tag += f" {smallest} is still emerging."
    b.callout(P(9, "callout") or tag)
    txt = (f"The global {root} market reflects a clear regional divide in demand patterns. {leader} remains the "
           f"largest region, accounting for {_usd(leader_d.get('value_usd_mn'))} or "
           f"{_pct(leader_d.get('share_pct_of_global'))} of global market value.")
    if len(reg_sorted) > 2:
        txt += (f" {reg_sorted[1][0]} ({_pct(reg_sorted[1][1].get('share_pct_of_global'))}) and {reg_sorted[2][0]} "
                f"({_pct(reg_sorted[2][1].get('share_pct_of_global'))}) follow")
        if len(reg_sorted) > 3:
            txt += f", while {_join([r for r, _ in reg_sorted[3:]])} together account for " \
                   f"{_pct(sum(d.get('share_pct_of_global', 0) for _, d in reg_sorted[3:]))}"
        txt += "."
    txt += f" {fastest} is projected to grow fastest, at {_pct(fastest_d.get('cagr_2025_2030_pct'))} a year through 2030."
    b.para(P(9, "p1") or txt)
    b.figure(charts / "fig8_1_regional.png", f"Regional market size {by} (bars) and projected CAGR through 2030 (line).")
    chart_idx = {r: i for i, r in enumerate(ordered_regions(regions), start=1)}
    rank = {r: i for i, (r, _) in enumerate(reg_sorted, start=1)}
    ordinal = {1: "largest", 2: "second-largest", 3: "third-largest", 4: "fourth-largest", len(rank): "smallest"}
    used_desc: set[str] = set()
    for rname in [r for r in TEMPLATE_REGION_ORDER if r in regions]:
        rd = regions[rname]
        rc = rd.get("cagr_2025_2030_pct", 0) or 0
        if rname == leader:
            desc = "The Largest Regional Market"
        elif rname == fastest:
            desc = "The Fastest-Growing Region"
        elif rname == smallest:
            desc = "A Small but Developing Market"
        elif rc > cagr:
            desc = "An Above-Average Growth Market"
        else:
            desc = "A Mature, Steady-Growth Market"
        rprose = PD(9, "regions", rname) or {}
        if isinstance(rprose, dict) and isinstance(rprose.get("descriptor"), str):
            ai_desc = rprose["descriptor"].strip().strip(".")
            if ai_desc.lower() not in used_desc:      # no two regions with the same title
                desc = ai_desc
        used_desc.add(desc.lower())
        b.heading(REGION_HEAD, f"{rname} \u2013 {desc}")
        countries = sorted(rd.get("countries") or [], key=lambda c: c.get("value_usd_mn", 0), reverse=True)
        rv = rd.get("value_usd_mn", 0) or 0
        txt = (f"{rname} represents the {ordinal.get(rank[rname], '')} {root} market, valued at {_usd(rv)} in {by} "
               f"({_pct(rd.get('share_pct_of_global'))} of global demand)")
        if countries and rv:
            txt += f", with {countries[0]['name']} contributing nearly {countries[0].get('value_usd_mn', 0) / rv * 100:.0f}% of regional demand"
        txt += (f". The region is projected to grow at {_pct(rc)} annually, reaching "
                f"{_usd(rd.get('forecast_value_usd_mn'))} by 2030.")
        txt = (rprose.get("para") if isinstance(rprose, dict) and isinstance(rprose.get("para"), str) else None) or txt
        b.para(txt)
        b.figure(charts / f"fig{8 + chart_idx[rname]}_1_region_{chart_idx[rname]}.png",
                 f"{rname}: market size by country and {low(mix_s)} mix, {by} estimate.")
        b.heading(KEYDYN_HEAD, f"Key Market Dynamics \u2013 {rname}")
        dyn = []
        mix = sorted(rd.get("application_mix_pct") or [], key=lambda m: m.get("share_pct", 0), reverse=True)
        if mix:
            dyn.append(f"{_short(mix[0]['name'])} accounts for nearly {mix[0].get('share_pct', 0):.0f}% of regional "
                       f"demand" + (f", followed by {_short(mix[1]['name'])} at {mix[1].get('share_pct', 0):.0f}%." if len(mix) > 1 else "."))
        dyn.append(f"Regional growth of {_pct(rc)} a year is {'above' if rc > cagr else 'below' if rc < cagr - 0.2 else 'in line with'} "
                   f"the {_pct(cagr)} global market CAGR.")
        if len(countries) > 1 and rv:
            two = (countries[0].get("value_usd_mn", 0) + countries[1].get("value_usd_mn", 0)) / rv * 100
            dyn.append(f"{countries[0]['name']} and {countries[1]['name']} together account for about {two:.0f}% of the regional market.")
        rb = rprose.get("bullets") if isinstance(rprose, dict) else None
        rb = [x for x in rb if isinstance(x, str)] if isinstance(rb, list) else []
        if rb:
            # The bullets sit under this region's chart (countries + mix), so
            # they must speak to it: add the data bullet for whichever side the
            # AI bullets leave out.
            said = " ".join(rb + [txt])
            if mix and not mentions(said, mix[0]["name"]):
                rb.insert(0, dyn[0])
            if len(countries) > 1 and rv and not mentions(said, countries[0]["name"]):
                rb.append(dyn[-1])
            rb = rb[:4]
        b.bullets(rb or dyn, proto=LIST_BULLET)

    # ===================================================================== #
    # 10. Competitive landscape
    # ===================================================================== #
    b.section_start(10, "Competitive Landscape", conf("competitive_landscape"))
    if companies:
        named = sum(c.get("share_pct", 0) for c in companies)
        b.para(P(10, "p1") or f"The global {root} market is {conc}, with the {len(companies)} named players controlling nearly "
               f"{_pct(named)} of total market value. Industry leadership is led by "
               f"{_join([c['name'] for c in companies[:3]])}, which together account for approximately "
               f"{_pct(top3)} of global revenue.")
        b.figure(charts / "fig14_1_competitive.png", f"Estimated market share by company, {by}.")
        b.h2("Market Leaders and Competitive Positioning")
        b.lead_bullets([(f"{c['name']} (~{c.get('share_pct', 0):.0f}% share):", _sentence(PD(10, "companies", c["name"]) or c.get("positioning_note", "")))
                        for c in companies[:5]])
        others = f.get("competitive_landscape", {}).get("others_share_pct", 0) or max(0.0, 100 - named)
        b.para(P(10, "p2") or f"The remaining {_pct(others)} of the market is held by smaller and regional players, "
               + ("leaving meaningful room for consolidation." if conc == "fragmented" else
                  "though scale advantages among the leaders limit their share gains."))

    # ===================================================================== #
    # 11. Regulation
    # ===================================================================== #
    areas = sorted(f.get("regulation", {}).get("compliance_areas", []) or [],
                   key=lambda a: a.get("cost_pct_of_cogs_high", 0), reverse=True)
    b.section_start(11, "Regulation and Compliance Overview", conf("regulation"))
    if areas:
        lo = min(a.get("cost_pct_of_cogs_low", 0) for a in areas)
        hi = max(a.get("cost_pct_of_cogs_high", 0) for a in areas)
        b.para(P(11, "p1") or f"Regulation acts as a built-in cost layer across the {root} industry, directly influencing margins, "
               f"pricing, and market entry dynamics. We estimate that individual compliance areas add between "
               f"{lo:.1f}% and {hi:.1f}% to cost of goods sold, depending on product type, certification "
               f"requirements, and regional regulatory standards.")
        b.figure(charts / "fig15_1_regulation.png",
                 "Compliance cost burden by regulatory area, as a percentage of cost of goods sold.")
        b.h2("Regulatory Complexity and Cost Burden Across Markets")
        items = [f"{_short(areas[0]['name'])} is the costliest pathway, adding an estimated "
                 f"{areas[0].get('cost_pct_of_cogs_low', 0):.1f}-{areas[0].get('cost_pct_of_cogs_high', 0):.1f}% to COGS."]
        if len(areas) > 2:
            items.append(f"{_short(areas[1]['name'])} and {_short(areas[2]['name'])} are the next largest cost areas, at up to "
                         f"{areas[1].get('cost_pct_of_cogs_high', 0):.1f}% and {areas[2].get('cost_pct_of_cogs_high', 0):.1f}% of COGS respectively.")
        if len(areas) > 1:
            items.append(f"{_short(areas[-1]['name'])} carries the lowest burden, at "
                         f"{areas[-1].get('cost_pct_of_cogs_low', 0):.1f}-{areas[-1].get('cost_pct_of_cogs_high', 0):.1f}% of COGS.")
        items.append(f"Across the {len(areas)} compliance areas we track, the burden falls most heavily on producers "
                     f"serving regulated, certification-intensive end markets.")
        b.bullets(PL(11, "bullets") or items)

    # ===================================================================== #
    # 12. End-user behaviour
    # ===================================================================== #
    segs = [s for s in f.get("willingness_to_pay", {}).get("consumer_segments", []) or [] if (s.get("baseline_usd") or 0) > 0]
    keys = {"with_clinical_evidence_usd": "proven performance evidence",
            "with_premium_positioning_usd": "premium positioning", "with_top_tier_format_usd": "a top-tier format"}
    b.section_start(12, "End User Behavior Outlook", conf("willingness_to_pay"))
    b.para(P(12, "p1") or f"Buyer behavior in the {root} market is increasingly segmented by willingness to pay, ranging from "
           f"price-sensitive buyers focused on cost to premium purchasers who pay more for evidence, quality, "
           f"and differentiated formats.")
    if not segs:
        b.para("The sources reviewed did not provide reliable willingness-to-pay figures for this market's buyer "
               "profiles, so price sensitivity is assessed qualitatively through the pricing tiers in Section 8 "
               "and the investment opportunities in Section 13.")
    if segs:
        b.subhead("Buyer Spending Patterns Are Becoming Increasingly Segmented")
        wtp_unit = f.get("willingness_to_pay", {}).get("unit") or "USD"
        lo_s = min(segs, key=lambda s: s["baseline_usd"])
        hi_s = max(segs, key=lambda s: s["baseline_usd"])
        b.para(P(12, "p2") or f"Willingness to pay varies significantly across buyer profiles. Baseline spend ranges from "
               f"{_price(lo_s['baseline_usd'])} {wtp_unit} for {_short(lo_s['name'])} to "
               f"{_price(hi_s['baseline_usd'])} {wtp_unit} for {_short(hi_s['name'])}, and every profile pays "
               f"more for at least one premium attribute.")
        b.figure(charts / "fig16_1_wtp.png",
                 f"Willingness to pay by buyer profile and product attribute, in {wtp_unit}.")
        b.subhead("Buyer Behavior, Pricing Power, and Innovation Trends")

        def best(s):
            return max(((s.get(k) or 0) / s["baseline_usd"], k) for k in keys)
        top = max(segs, key=lambda s: best(s)[0])
        prem, attr = best(top)
        avg = {k: sum((s.get(k) or 0) / s["baseline_usd"] for s in segs) / len(segs) for k in keys}
        strongest = max(avg, key=avg.get)
        b.bullets(PL(12, "bullets") or [
            f"{_short(top['name'])} show the largest premium: up to {(prem - 1):.0%} above baseline for {keys[attr]}.",
            f"Across buyer profiles, {keys[strongest]} adds the most value on average, lifting willingness to pay by "
            f"about {(avg[strongest] - 1):.0%}.",
            "Pricing power remains strongest where buyers pay for proven performance and differentiated formats, "
            "supporting higher margins than commodity tiers.",
        ])

    # ===================================================================== #
    # 13. Investment priorities
    # ===================================================================== #
    opps = sorted(f.get("investment_priorities", {}).get("opportunities", []) or [],
                  key=lambda o: o.get("roi_high_pct", 0), reverse=True)
    b.section_start(13, "Strategic Investment Priorities", conf("investment_priorities"))
    b.para(P(13, "p1") or f"The {root} market offers multiple investment pathways, but returns vary significantly depending on "
           f"where participants operate within the value chain. The most attractive opportunities over the next "
           f"five years are expected to come from "
           f"{_join([_short(o.get('opportunity', '')).split(' for ')[0] for o in opps[:3]])}.")
    if opps:
        def roi_label(o):
            mid = (o.get("roi_low_pct", 0) + o.get("roi_high_pct", 0)) / 2
            return "HIGH" if mid >= 35 else "MED" if mid >= 20 else "LOW"
        b.table(GTM_TABLE, [[o.get("opportunity", ""), o.get("target_segment", ""), o.get("time_to_market", ""),
                             f"{roi_label(o)} ({o.get('roi_low_pct', 0):.0f}-{o.get('roi_high_pct', 0):.0f}%)",
                             o.get("capital_intensity", "")] for o in opps])
        b.add(b.clone(SPACER))
        b.table_caption("Go-to-market strategy matrix, ranked by expected ROI and time-to-market.")
        b.subhead("Highest-Potential Investment Opportunities")
        fastest_o = min(opps, key=lambda o: int(str(o.get("time_to_market", "99")).split("-")[0] or 99))
        light = [o for o in opps if str(o.get("capital_intensity", "")).lower().startswith("low")]
        items = [f"{_short(opps[0].get('opportunity'))} offers the highest expected return, at "
                 f"{opps[0].get('roi_low_pct', 0):.0f}-{opps[0].get('roi_high_pct', 0):.0f}% ROI, targeting "
                 f"{_short(opps[0].get('target_segment'))}."]
        light = [o for o in light if o is not opps[0] and o is not fastest_o]
        if fastest_o is not opps[0]:
            items.append(f"{_short(fastest_o.get('opportunity'))} is the fastest route to market, at "
                         f"{fastest_o.get('time_to_market')}, with {fastest_o.get('roi_low_pct', 0):.0f}-"
                         f"{fastest_o.get('roi_high_pct', 0):.0f}% ROI.")
        if light:
            items.append(f"{_short(light[0].get('opportunity'))} requires the least capital "
                         f"({light[0].get('capital_intensity')}) while still returning "
                         f"{light[0].get('roi_low_pct', 0):.0f}-{light[0].get('roi_high_pct', 0):.0f}%.")
        b.bullets(PL(13, "bullets") or items)

    # ===================================================================== #
    # 14. Scenarios
    # ===================================================================== #
    sc = f.get("scenarios", {}) or {}
    bear, base, bull = sc.get("bear", {}), sc.get("base", {}), sc.get("bull", {})
    b.section_start(14, "Scenario Analysis: Three Possible Market Futures", conf("scenarios"))
    b.para(P(14, "p1") or f"Our base-case forecast projects the global {root} market to grow at a {_pct(base.get('cagr_pct', cagr))} "
           f"CAGR, reaching {_usd(base.get('terminal_value_usd_mn', n['fc_val']))} by {fy}. However, long-term "
           f"outcomes remain sensitive to {dn[0] if dn else 'demand growth'} on the upside and "
           f"{rn[0] if rn else 'structural headwinds'} on the downside. To reflect this uncertainty, we model three "
           f"realistic market scenarios spanning bullish, base-case, and downside growth trajectories.")
    b.figure(charts / "fig18_1_scenarios.png", f"Bear, base, and bull case forecasts, {by} to {fy}.")
    b.subhead("Scenario Outlook: Bear, Base, and Bull Case Futures")
    b.lead_bullets([
        (f"Bear Case – {_usd_title(bear.get('terminal_value_usd_mn'))} by {fy} ({_pct(bear.get('cagr_pct'))} CAGR):",
         P(14, "bear") or f"{_cap(_join(rn[:2]) or 'structural headwinds')} prove more severe than expected, while "
         f"{dn[0] if dn else 'demand'} loses momentum, significantly slowing market growth."),
        (f"Base Case – {_usd_title(base.get('terminal_value_usd_mn'))} by {fy} ({_pct(base.get('cagr_pct'))} CAGR):",
         P(14, "base") or f"{_cap(dn[0]) if dn else 'Demand'} continues at its current pace and the drag from "
         f"{rn[0] if rn else 'headwinds'} stays contained, supporting steady long-term expansion."),
        (f"Bull Case – {_usd_title(bull.get('terminal_value_usd_mn'))} by {fy} ({_pct(bull.get('cagr_pct'))} CAGR):",
         P(14, "bull") or f"Faster momentum from {_join(dn[:2]) or 'demand'}, combined with easing pressure from "
         f"{rn[0] if rn else 'headwinds'}, could materially strengthen market growth"
         + (f", with {_short(fast_app['name'])} potentially expanding well above {_pct(fast_app.get('cagr_2025_2030_pct'))} annually."
            if fast_app else ".")),
    ])

    # ===================================================================== #
    # 15. Investment thesis
    # ===================================================================== #
    b.section_start(15, "Investment Thesis: Where Long-Term Value Creation Will Come From", conf("market_size"))
    b.para(P(15, "p1") or f"The {root} market presents a {'structurally attractive' if cagr >= 5 else 'steady, moderate-growth'} "
           f"long-term opportunity driven by {_join(dn[:2])}. Over the next decade, value creation is expected to "
           f"concentrate in {_short(fast_app.get('name', ''))} and in {leader}, where growth and scale "
           f"advantages are strongest.")
    thesis = [
        ("Steady Structural Growth:", f"The global {root} market is projected to grow at a {_pct(cagr)} CAGR through "
                                      f"{fy}, creating nearly {n['incremental_fmt']} in incremental market opportunity."),
    ]
    if fast_app:
        thesis.append((f"{_short(fast_app['name'])} Will Outgrow the Market:",
                       f"At {_pct(fast_app.get('cagr_2025_2030_pct'))} a year, it is the fastest-growing application, "
                       f"ahead of the {_pct(cagr)} market average."))
    thesis.append((f"{leader} Remains the Center of Gravity:",
                   f"With {_pct(leader_d.get('share_pct_of_global'))} of global value, {leader} will shape pricing, "
                   f"capacity, and competitive strategy across the industry."))
    if companies:
        thesis.append((f"Competition Is {conc.title().replace(' ', ' ')}:",
                       f"The top three players hold about {_pct(top3)} of revenue"
                       + (", leaving room for focused challengers and consolidation." if conc == "fragmented" else
                          ", so scale and differentiation will decide who captures disproportionate returns.")))
    b.lead_bullets(LB(15, "thesis") or thesis, proto=THESIS_LEAD)
    b.add(b.set_text(b.clone(CONCLUDING_HEAD), "Concluding View"))
    b.para(P(15, "concluding") or f"The {root} market remains a {'structurally attractive' if cagr >= 5 else 'resilient'} long-term "
           f"opportunity, combining {_pct(cagr)} compounded growth with clear pockets of outperformance in "
           f"{_short(fast_app.get('name', ''))} and {leader}. Companies positioned in the fastest-growing "
           f"applications, with a credible plan to manage {rn[0] if rn else 'structural headwinds'}, are expected to "
           f"capture disproportionate growth and profitability over the next decade.")

    # ---- footer: market name + month (the PAGE field is left untouched)
    for sect in b.doc.sections:
        for p in sect.footer.paragraphs:
            for r in p.runs:
                if "White Paper" in (r.text or ""):
                    r.text = f"Global {root} Market  -  White Paper  -  {month}"
                    if len(r.text) > 45:
                        tabs = [x for x in p.runs if x._r.find(qn("w:tab")) is not None]
                        if len(tabs) > 1:
                            p._p.remove(tabs[0]._r)

    b.sync_toc()
    out = market_dir / f"{slug}-whitepaper.docx"
    b.save(out)
    for line in qa(b, n, data):
        print(line)
    return out


_CONC_RE = re.compile(r"\b(highly concentrated|moderately concentrated|fragmented)\b", re.I)


def qa(b: Builder, n: dict, data: dict) -> list[str]:
    """Post-build checks for the problems that kept recurring: repeated
    headings, template wording that doesn't match this market, the headline
    size restated section after section, and contradicting the competition
    label. Returns printable lines (does not fail the build)."""
    issues = []
    heads = [(sec, h.strip().rstrip(":")) for sec, h in b.heads if h.strip()]
    seen: dict[str, int] = {}
    for sec, h in heads:
        key = re.sub(r"\W+", " ", h.lower()).strip()
        if key in seen:
            issues.append(f"duplicate heading (sections {seen[key]} and {sec}): {h}")
        seen.setdefault(key, sec)
    for i, (s1, h1) in enumerate(heads):
        for s2, h2 in heads[i + 1:]:
            if s1 != s2 and similar_heading(h1, h2, b.market) and h1.lower() != h2.lower():
                issues.append(f"near-duplicate headings (sections {s1} and {s2}): '{h1}' / '{h2}'")

    fc = n["fc_val_fmt"].replace("USD ", "")
    restated = sorted({sec for sec, t in b.texts if sec not in (1, 2, 14, 15) and fc in t})
    if restated:
        issues.append(f"headline forecast ({fc}) restated in section(s) {restated}")

    comps = (n["fields"].get("competitive_landscape") or {}).get("companies") or []
    conc = concentration_label(sum(c.get("share_pct", 0) for c in comps[:3]))
    for sec, t in b.texts:
        for m in _CONC_RE.finditer(t):
            if m.group(1).lower() != conc and re.search(r"compet|player|compan|landscape|share", t, re.I):
                issues.append(f"section {sec} calls competition '{m.group(1)}' but top-3 share makes it {conc}")
                break

    for sec, t in b.texts:
        if "vitamin c" in t.lower():
            issues.append(f"section {sec} still mentions Vitamin C: {t[:70]}")

    out = [f"QA: {len(issues)} issue(s)" + (":" if issues else " -- headings unique, captions numbered by section")]
    out += [f"  - {i}" for i in issues]
    out += [f"  (left out) {s}" for s in b.skipped]
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--market-slug", required=True)
    args = p.parse_args()
    out = build(args.market_slug)
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
