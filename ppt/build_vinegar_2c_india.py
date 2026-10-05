"""Approach 2C walkthrough: India, white vinegar, one row per use case.

Product type (white / synthetic vinegar) x use case (ketchup, mayonnaise,
cakes, ...) x country (India). Each use case is

    host product volume x vinegar dose x adoption  ->  tonnes of vinegar
    tonnes x white-vinegar price                   ->  revenue

and the use cases are reconciled against a top-down anchor (the India vinegar
market value). Every input below carries its source URL, the figure found at
that URL when it was checked, and a status:

    verified     the number used is the number published at the URL
    derived      computed from verified numbers with the stated formula
    range        published as a range; the value used sits inside it
    assumption   no published figure exists; stated and labelled

No LLM produces any number here. The arithmetic is plain Python.

    python ppt/build_vinegar_2c_india.py
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ppt"))

from build_ppt_artifacts import render  # noqa: E402

CHECKED = "2026-10-04"

# --------------------------------------------------------------------------- #
# Inputs (value used, what the source says, where, status)
# --------------------------------------------------------------------------- #
S = {
    "comtrade_imports_t": dict(
        value=1225.0, unit="t", status="verified",
        found="1,224,998 kg imported, CIF US$1,873,026 (HS 2209, India, 2024)",
        url="https://comtradeapi.un.org/public/v1/preview/C/A/HS?reporterCode=699&period=2024&cmdCode=2209&flowCode=M&partnerCode=0",
        source="UN Comtrade (live API)"),
    "comtrade_exports_t": dict(
        value=963.0, unit="t", status="verified",
        found="963,026 kg exported, FOB US$858,031 (HS 2209, India, 2024; weight estimated by Comtrade)",
        url="https://comtradeapi.un.org/public/v1/preview/C/A/HS?reporterCode=699&period=2024&cmdCode=2209&flowCode=X&partnerCode=0",
        source="UN Comtrade (live API)"),
    "india_vinegar_usd_mn": dict(
        value=301.61, unit="US$ Mn", status="verified",
        found="India vinegar market size for 2024: US$ 301.61 million (to US$ 454.64 Mn by 2031)",
        url="https://www.gii.tw/report/qyr1697967-global-india-vinegar-market-report-forecast.html",
        source="QYResearch (via GII)"),
    "synthetic_share": dict(
        value=0.80, unit="share", status="assumption",
        found="Synthetic segment held ~80% (79.9%) of the GLOBAL vinegar market in 2024 — applied to India here; no India split is published",
        url="https://www.gminsights.com/industry-analysis/vinegar-market",
        source="Global Market Insights"),
    "usd_inr": dict(
        value=83.67, unit="INR per USD", status="verified",
        found="The average level of USD/INR for 2024 was 83.6713",
        url="https://www.poundsterlinglive.com/history/USD-INR-2024",
        source="Pound Sterling Live (2024 USD/INR history)"),
    "retail_price_inr_l": dict(
        value=70.0, unit="₹ per litre", status="range",
        found="₹42.25/L bulk (1,000 L MOQ, Hyderabad) to ₹98/L consumer bottle (₹60 per 610 ml, Ajmer); ₹70 is the midpoint used to turn market VALUE into VOLUME",
        url="https://m.indiamart.com/impcat/synthetic-vinegar.html",
        source="IndiaMART / GlobalLinker listings"),
    "bulk_price_inr_l": dict(
        value=42.25, unit="₹ per litre", status="verified",
        found="Wholesale synthetic vinegar ₹42.25/litre, minimum order 1,000 litres (Hyderabad)",
        url="https://www.globallinker.com/products/synthetic-vinegar-8622282",
        source="GlobalLinker listing"),
    "ketchup_t": dict(
        value=1_200_000.0, unit="t", status="verified",
        found="India consumption of tomato ketchup and tomato sauces reached 1.2 million tons in 2024 (production 1.3 million tons)",
        url="https://www.indexbox.io/blog/tomato-ketchup-world-market-overview-2024-4/",
        source="IndexBox"),
    "ketchup_dose": dict(
        value=0.08, unit="share of product weight", status="range",
        found="Commercial ketchup contains 6–8% vinegar (7–12% in some formulation guides); 8% used",
        url="https://spice.alibaba.com/spice-basics/how-tomato-ketchup-is-made",
        source="Ketchup formulation guides"),
    "ketchup_adoption": dict(
        value=0.70, unit="share of volume", status="assumption",
        found="No published figure. Some Indian tomato sauces use acetic acid instead of vinegar; 70% assumed to use vinegar",
        url="", source="Assumption"),
    "mayo_usd_mn": dict(
        value=428.0, unit="US$ Mn", status="verified",
        found="The India mayonnaise market size reached USD 428.0 Million in 2024",
        url="https://www.imarcgroup.com/india-mayonnaise-market",
        source="IMARC Group"),
    "mayo_price_inr_kg": dict(
        value=180.0, unit="₹ per kg", status="assumption",
        found="₹100 per 875 g pack wholesale (≈ ₹114/kg, IndiaMART); retail packs sell higher. ₹180/kg assumed for value-to-volume",
        url="https://www.indiamart.com/didwaniasales/veg-mayonnaise.html",
        source="IndiaMART listing + assumption"),
    "mayo_dose": dict(
        value=0.054, unit="share of product weight", status="verified",
        found="Formulation: 65% oil, 10% egg, 5.4% vinegar, 1.5% salt, 1% sugar, 1% spices, 16% water",
        url="https://pmc.ncbi.nlm.nih.gov/articles/PMC7931383/",
        source="Peer-reviewed mayonnaise-type sauce study (PMC7931383, Table 5)"),
    "cake_t": dict(
        value=378_000.0, unit="t", status="verified",
        found="India cake market volume 378 thousand MT in 2025 (another edition of the same publisher quotes 455 thousand t)",
        url="https://www.kenresearch.com/industry-reports/india-cake-market.md",
        source="Ken Research"),
    "eggless_usd_mn": dict(
        value=940.5, unit="US$ Mn", status="verified",
        found="India eggless cakes market: USD 940.5 million in 2026",
        url="https://www.futuremarketinsights.com/reports/india-eggless-cakes-market",
        source="Future Market Insights"),
    "cake_usd_mn": dict(
        value=2380.0, unit="US$ Mn", status="verified",
        found="India cake market size (2026): USD 2.38 Billion",
        url="https://www.mordorintelligence.com/industry-reports/india-cake-market",
        source="Mordor Intelligence"),
    "cake_dose": dict(
        value=0.020, unit="share of batter weight", status="derived",
        found="Eggless sponge recipe: 1 tbsp (≈15 g) white vinegar in ≈730 g batter (1.5 cups flour, 1 cup sugar, 1 cup milk, ¼ cup oil, cocoa, soda) = 2.0%",
        url="https://cookingandme.com/eggless-chocolate-sponge-cake-eggless-recipes/",
        source="Recipe (home scale)"),
}

# Eggless share is derived from two verified values.
EGGLESS_SHARE = S["eggless_usd_mn"]["value"] / S["cake_usd_mn"]["value"]
DENSITY = 1.01  # kg per litre of 4-5% vinegar


def t_to_litres(t: float) -> float:
    return t * 1000 / DENSITY


def inr_cr(inr: float) -> float:
    return inr / 1e7


def usd_mn_from_inr(inr: float) -> float:
    return inr / S["usd_inr"]["value"] / 1e6


def fmt(v: float, d: int = 0) -> str:
    return f"{v:,.{d}f}"


# --------------------------------------------------------------------------- #
# Calculation (plain arithmetic)
# --------------------------------------------------------------------------- #
net_trade_t = S["comtrade_imports_t"]["value"] - S["comtrade_exports_t"]["value"]
white_usd_mn = S["india_vinegar_usd_mn"]["value"] * S["synthetic_share"]["value"]
white_inr = white_usd_mn * 1e6 * S["usd_inr"]["value"]
anchor_l = white_inr / S["retail_price_inr_l"]["value"]
anchor_t = anchor_l * DENSITY / 1000

mayo_t = S["mayo_usd_mn"]["value"] * 1e6 * S["usd_inr"]["value"] / S["mayo_price_inr_kg"]["value"] / 1000
rows = [
    dict(id="uc_ketchup", name="Ketchup & tomato sauces", host_label="Ketchup & tomato sauce consumed",
         host_t=S["ketchup_t"]["value"], host_key="ketchup_t", dose=S["ketchup_dose"]["value"], dose_key="ketchup_dose",
         adopt=S["ketchup_adoption"]["value"], adopt_key="ketchup_adoption", adopt_label="share made with vinegar"),
    dict(id="uc_mayo", name="Mayonnaise", host_label="Mayonnaise (US$428 Mn ÷ ₹180/kg)",
         host_t=mayo_t, host_key="mayo_usd_mn", dose=S["mayo_dose"]["value"], dose_key="mayo_dose",
         adopt=1.0, adopt_key=None, adopt_label="all mayonnaise uses vinegar"),
    dict(id="uc_cake", name="Cakes (eggless)", host_label="Cakes produced",
         host_t=S["cake_t"]["value"], host_key="cake_t", dose=S["cake_dose"]["value"], dose_key="cake_dose",
         adopt=EGGLESS_SHARE, adopt_key=None, adopt_label="eggless share (vinegar + soda replaces egg)"),
]
for r in rows:
    r["vinegar_t"] = r["host_t"] * r["dose"] * r["adopt"]
    r["litres"] = t_to_litres(r["vinegar_t"])
    r["inr"] = r["litres"] * S["bulk_price_inr_l"]["value"]
    r["usd_mn"] = usd_mn_from_inr(r["inr"])
sized_t = sum(r["vinegar_t"] for r in rows)
sized_inr = sum(r["inr"] for r in rows)
residual_t = anchor_t - sized_t

NOT_SIZED = [
    ("Household cooking", "Households (288.7 Mn, Statista 2024) × % buying vinegar × litres/yr — no penetration or usage survey published"),
    ("Indo-Chinese restaurants & street food", "Chinese is ~33% of restaurant types (GlobalData) — but no count of outlets or litres per outlet is published"),
    ("Chinese sauces (chilli, schezwan, soy blends)", "Market grew >17% CAGR (Bonafide) — no tonnage published"),
    ("Pickles & preserves", "Most Indian pickles are oil/salt based; vinegar share not published"),
    ("Household cleaning", "No usage data published"),
    ("Industrial (pharma, textiles)", "Usually acetic acid, not vinegar; no data"),
]


# --------------------------------------------------------------------------- #
# Steps for the walkthrough engine
# --------------------------------------------------------------------------- #
def src(key: str) -> dict:
    s = S[key]
    return {"name": f"{s['source']} — {s['status'].upper()}", "url": s["url"], "excerpt": s["found"],
            "extractedValue": fmt(s["value"], 3 if s["value"] < 10 else 1).rstrip("0").rstrip("."), "unit": s["unit"],
            "decision": "rejected" if s["status"] == "assumption" else "accepted",
            **({"rejectionReason": "No published figure — labelled assumption"} if s["status"] == "assumption" else {})}


def tag(key: str) -> str:
    return {"verified": "web", "derived": "derived", "range": "web", "assumption": "assumption"}[S[key]["status"]]


NOTE = f"Every input checked online on {CHECKED}; see the Source Audit step."
steps = [
    {
        "id": "setup", "name": "Market Setup", "data_tag": "real", "stage": "market", "source": "human",
        "data_tag_note": NOTE,
        "agent": {"name": "Analyst", "role": "Fixes the cell being sized: product type × use case × country."},
        "inputShort": "Worked example", "outputLabel": "White vinegar · India · 2024",
        "reason": "2C = product type × use case × country. This page builds the India white-vinegar column.",
        "output": [{"l": "Country", "v": "India"}, {"l": "Product type", "v": "White (synthetic) vinegar"},
                   {"l": "Base year", "v": "2024 (some inputs 2025–26, flagged)"}, {"l": "Units", "v": "tonnes, ₹ crore, US$ Mn"}],
        "activity": ["Choosing country, product type, base year"],
    },
    {
        "id": "trade", "name": "Trade Check: Is India Self-Sufficient?", "data_tag": "real", "stage": "anchor", "source": "web",
        "data_tag_note": "Fetched live from the UN Comtrade public API (HS 2209, 2024).",
        "agent": {"name": "Code — UN Comtrade API", "role": "Imports and exports of vinegar, straight from customs data. No LLM, no web search."},
        "inputShort": "HS 2209, India, 2024", "outputLabel": f"Net imports {fmt(net_trade_t)} t",
        "reason": "Trade is tiny, so almost all Indian vinegar is made in India. The anchor therefore has to come from the domestic market value, not from trade.",
        "flowCalc": {"a": {"value": f"{fmt(S['comtrade_imports_t']['value'])} t", "unit": "imports", "src": "web"},
                     "opLabel": "−", "b": {"value": f"{fmt(S['comtrade_exports_t']['value'])} t", "unit": "exports", "src": "web"},
                     "result": {"value": f"{fmt(net_trade_t)} t", "src": "code"},
                     "final": {"big": f"{fmt(net_trade_t)} t", "cap": "net imports — under 0.1% of the market"}},
        "sources": [src("comtrade_imports_t"), src("comtrade_exports_t")],
        "activity": ["Calling Comtrade API", "Imports − exports"],
    },
    {
        "id": "anchor", "name": "Top-Down Anchor: White Vinegar in India", "data_tag": "real", "stage": "anchor", "source": "derived",
        "data_tag_note": NOTE,
        "agent": {"name": "Code", "role": "India vinegar market value × synthetic share ÷ price per litre = litres. This is the ceiling every use case must fit under."},
        "inputShort": f"US${S['india_vinegar_usd_mn']['value']} Mn × {S['synthetic_share']['value']:.0%}",
        "outputLabel": f"≈ {fmt(anchor_t / 1000)} thousand t",
        "reason": "The use-case rows below must add up to this. Whatever they don't explain is shown as 'not yet sized'.",
        "flowCalc": {"a": {"value": f"US${white_usd_mn:,.1f} Mn", "unit": f"white vinegar value (US${S['india_vinegar_usd_mn']['value']} Mn × 80%)", "src": "web"},
                     "opLabel": "÷", "b": {"value": f"₹{S['retail_price_inr_l']['value']:.0f}/L", "unit": "average price (₹42–98 range)", "src": "web"},
                     "result": {"value": f"{anchor_l / 1e6:,.0f} Mn L", "src": "code"},
                     "final": {"big": f"≈ {fmt(anchor_t / 1000)} thousand t", "cap": "white vinegar consumed in India (anchor)"}},
        "sources": [src("india_vinegar_usd_mn"), src("synthetic_share"), src("retail_price_inr_l"), src("usd_inr")],
        "researcher_note": {"note": "Weakest link of the anchor: the 80% synthetic share is a GLOBAL figure applied to India, and the price used to turn value into litres spans ₹42–98/L. Either can move the anchor by ±40%."},
        "activity": ["Value × synthetic share", "Converting to litres with ₹/L", "Converting to tonnes"],
    },
    {
        "id": "usecases", "name": "White Vinegar Use Cases", "data_tag": "real", "stage": "uses", "source": "human",
        "data_tag_note": "Which use cases have published volume data and which don't.",
        "agent": {"name": "Analyst", "role": "Lists every use of white vinegar and checks whether a host-product volume exists."},
        "inputShort": "White vinegar, India", "outputLabel": f"{len(rows)} sizable · {len(NOT_SIZED)} need primary research",
        "reason": "Only rows with published host volumes are sized. The rest stay in the residual instead of being guessed.",
        "output": [f"✓ {r['name']} — sizable" for r in rows] + [f"✗ {n} — no data" for n, _ in NOT_SIZED],
        "validation": [{"label": r["name"], "passed": True, "detail": "published host-product volume or value exists"} for r in rows]
        + [{"label": n, "passed": False, "detail": why} for n, why in NOT_SIZED],
        "activity": ["Listing use cases", "Checking which have published data"],
    },
]

for r in rows:
    srcs = [src(r["host_key"]), src(r["dose_key"])]
    if r["adopt_key"]:
        srcs.append(src(r["adopt_key"]))
    if r["id"] == "uc_mayo":
        srcs.append(src("mayo_price_inr_kg"))
    if r["id"] == "uc_cake":
        srcs += [src("eggless_usd_mn"), src("cake_usd_mn")]
    adopt_txt = f"{r['adopt']:.1%}" if r["adopt"] < 1 else "100%"
    steps.append({
        "id": r["id"], "name": f"Use Case: {r['name']}",
        "data_tag": "real", "stage": "uses", "source": "derived",
        "data_tag_note": NOTE,
        "agent": {"name": "Code", "role": "host product tonnes × vinegar dose × adoption = tonnes of white vinegar."},
        "inputShort": f"{fmt(r['host_t'] / 1000)} thousand t host product",
        "outputLabel": f"{fmt(r['vinegar_t'])} t vinegar",
        "reason": {
            "uc_ketchup": "The single biggest sizable use: vinegar is a core ketchup ingredient.",
            "uc_mayo": "Mayonnaise volume isn't published, so it is derived from market value ÷ price per kg.",
            "uc_cake": f"Only eggless cakes use vinegar (with baking soda, replacing egg). Eggless share = US${S['eggless_usd_mn']['value']} Mn ÷ US${S['cake_usd_mn']['value']:,.0f} Mn = {EGGLESS_SHARE:.1%}.",
        }[r["id"]],
        "flowCalc": {"a": {"value": f"{fmt(r['host_t'] / 1000)}k t", "unit": r["host_label"], "src": tag(r["host_key"])},
                     "opLabel": f"× {r['dose']:.1%} dose ×",
                     "b": {"value": adopt_txt, "unit": r["adopt_label"], "src": tag(r["adopt_key"]) if r["adopt_key"] else "derived"},
                     "result": {"value": f"{fmt(r['vinegar_t'])} t", "src": "code"},
                     "final": {"big": f"{fmt(r['vinegar_t'])} t", "cap": f"white vinegar used in {r['name'].lower()}"}},
        "calculation": {"inputs": [{"label": r["host_label"], "value": f"{fmt(r['host_t'])} t"},
                                   {"label": "Vinegar dose", "value": f"{r['dose']:.1%} of product weight ({S[r['dose_key']]['status']})"},
                                   {"label": "Adoption", "value": f"{adopt_txt} — {r['adopt_label']}"}],
                        "formula": "vinegar_t = host_t × dose × adoption",
                        "output": f"{fmt(r['vinegar_t'])} t ≈ {r['litres'] / 1e6:,.2f} Mn litres"},
        "sources": srcs,
        "activity": ["Host volume × dose × adoption"],
    })

steps.append({
    "id": "price", "name": "Price → Revenue per Use Case", "data_tag": "real", "stage": "value", "source": "web",
    "data_tag_note": NOTE,
    "agent": {"name": "Code", "role": "Litres × bulk price (food manufacturers buy in bulk) = revenue to vinegar makers."},
    "inputShort": f"₹{S['bulk_price_inr_l']['value']}/L bulk", "outputLabel": f"₹{inr_cr(sized_inr):,.0f} crore sized",
    "reason": "Manufacturers buy bulk, so the bulk B2B price is used, not the shelf price.",
    "aggregations": [{"title": "Use case revenue (₹ crore, at ₹42.25/L)",
                      "rows": [{"label": f"{r['name']} — {fmt(r['vinegar_t'])} t", "value": f"₹{inr_cr(r['inr']):,.1f} cr"} for r in rows],
                      "totalLabel": "Sized use cases", "total": f"₹{inr_cr(sized_inr):,.1f} cr (US${usd_mn_from_inr(sized_inr):,.1f} Mn)"}],
    "sources": [src("bulk_price_inr_l"), src("usd_inr")],
    "activity": ["Tonnes → litres", "Litres × ₹/L"],
})
steps.append({
    "id": "reconcile", "name": "Reconciliation: Use Cases vs Anchor", "data_tag": "real", "stage": "reconcile", "source": "code",
    "data_tag_note": "The bottom-up rows must fit under the top-down anchor; the gap is reported, not hidden.",
    "agent": {"name": "Code", "role": "Anchor − Σ sized use cases = not-yet-sized uses."},
    "inputShort": f"{fmt(sized_t)} t sized vs {fmt(anchor_t)} t anchor", "outputLabel": f"{sized_t / anchor_t:.0%} explained",
    "big": {"value": f"{sized_t / anchor_t:.0%} explained", "unit": "of the India white-vinegar anchor"},
    "reason": "If the rows exceeded the anchor, a dose would be too high. Here they explain part of it; the rest needs primary research.",
    "aggregations": [{"title": "Where India's white vinegar goes (tonnes)",
                      "rows": [{"label": r["name"], "value": f"{fmt(r['vinegar_t'])} t"} for r in rows]
                      + [{"label": "Not yet sized (household, restaurants, Chinese sauces, pickles, cleaning)", "value": f"{fmt(residual_t)} t"}],
                      "totalLabel": "Anchor", "total": f"{fmt(anchor_t)} t"}],
    "validation": [{"label": "Sized rows fit under the anchor", "passed": sized_t <= anchor_t, "detail": f"{fmt(sized_t)} t ≤ {fmt(anchor_t)} t"},
                   {"label": "Share explained by published data", "passed": sized_t / anchor_t >= 0.5, "detail": f"{sized_t / anchor_t:.0%} — the rest needs survey data"}],
    "activity": ["Summing sized rows", "Anchor − sized = residual"],
})

cake = next(r for r in rows if r["id"] == "uc_cake")
steps.append({
    "id": "final_market_size", "name": "Result: White Vinegar in Cakes, India", "data_tag": "real", "stage": "final", "source": "code",
    "data_tag_note": NOTE,
    "agent": {"name": "Code", "role": "One 2C cell: product type × use case × country."},
    "inputShort": f"{fmt(cake['vinegar_t'])} t × ₹{S['bulk_price_inr_l']['value']}/L", "outputLabel": f"₹{inr_cr(cake['inr']):,.1f} crore",
    "reason": "A real, sourced number for one 2C cell — and a sense of how small individual cells are.",
    "big": {"value": f"₹{inr_cr(cake['inr']):,.1f} crore", "unit": f"≈ US${cake['usd_mn']:,.2f} Mn · {fmt(cake['vinegar_t'])} t of white vinegar in eggless cakes, India"},
    "calculation": {"inputs": [{"label": "Cakes produced (2025)", "value": f"{fmt(cake['host_t'])} t (Ken Research)"},
                               {"label": "Eggless share", "value": f"{EGGLESS_SHARE:.1%} (US$940.5 Mn ÷ US$2,380 Mn)"},
                               {"label": "Vinegar dose", "value": f"{cake['dose']:.1%} of batter (recipe)"},
                               {"label": "Bulk white vinegar price", "value": f"₹{S['bulk_price_inr_l']['value']}/L"}],
                    "formula": "vinegar_t = cakes_t × eggless_share × dose\nrevenue = vinegar_t × 1000 ÷ density × ₹/L",
                    "output": f"{fmt(cake['host_t'])} × {EGGLESS_SHARE:.3f} × {cake['dose']:.3f} = {fmt(cake['vinegar_t'])} t → {cake['litres'] / 1e6:,.2f} Mn L × ₹{S['bulk_price_inr_l']['value']} = ₹{inr_cr(cake['inr']):,.1f} crore"},
    "aggregations": [{"title": "All sized white-vinegar use cases (₹ crore)",
                      "rows": [{"label": r["name"], "value": f"₹{inr_cr(r['inr']):,.1f} cr"} for r in rows],
                      "totalLabel": "Sized total", "total": f"₹{inr_cr(sized_inr):,.1f} cr"}],
    "is_final": True,
    "activity": ["Cake cell computed", "All sized rows totalled"],
})

audit = []
for key, s in S.items():
    audit.append({"label": f"{s['source']}: {fmt(s['value'], 3 if s['value'] < 10 else 1)} {s['unit']}",
                  "passed": s["status"] in ("verified", "derived", "range"),
                  "detail": f"{s['status'].upper()} — {s['found']}" + (f" ({s['url']})" if s["url"] else "")})
counts = {k: sum(1 for s in S.values() if s["status"] == k) for k in ("verified", "derived", "range", "assumption")}
steps.append({
    "id": "audit", "name": "Source Audit (±10% Check)", "data_tag": "real", "stage": "final", "source": "web",
    "data_tag_note": f"Each figure was re-opened at its URL on {CHECKED}.",
    "agent": {"name": "Analyst", "role": "Re-checks every input against its source. Verified = same as published (0% difference)."},
    "inputShort": f"{len(S)} inputs", "outputLabel": ", ".join(f"{v} {k}" for k, v in counts.items()),
    "reason": "Assumptions are the inputs no source publishes — they cannot be checked to ±10% and are shown as such.",
    "output": [{"l": k.title(), "v": str(v)} for k, v in counts.items()],
    "validation": audit,
    "activity": ["Re-opening each URL", "Comparing value used vs published"],
})

STAGES = [{"key": k, "label": l} for k, l in (
    ("market", "Cell"), ("anchor", "Top-Down Anchor"), ("uses", "Use Cases"), ("value", "Revenue"),
    ("reconcile", "Reconcile"), ("final", "Result"))]


def main() -> int:
    path = render(
        "2C_Vinegar_India_White_Vinegar_Walkthrough.html",
        title="2C — White Vinegar in India, by Use Case",
        h1="Approach 2C — India White Vinegar: Product Type × Use Case",
        subtitle="Each use case: host product volume × vinegar dose × adoption = tonnes of vinegar; × price = revenue. All use cases are reconciled against the India market anchor.",
        tab="2C · White Vinegar × Use Case × India",
        desc="White vinegar → use cases (ketchup, mayonnaise, cakes, …) → host volume × dose × adoption → tonnes → × price → revenue → reconcile against the India anchor.",
        stages=STAGES, steps=steps, flags=[], badge_real="Sourced & Checked",
        banner=("real", f"Sourced inputs · checked online {CHECKED} · {counts['verified']} verified, {counts['derived']} derived, {counts['range']} range, {counts['assumption']} assumptions"),
    )
    data = {"inputs": S, "eggless_share": EGGLESS_SHARE, "anchor_t": anchor_t, "rows": rows,
            "sized_t": sized_t, "residual_t": residual_t, "sized_inr_cr": inr_cr(sized_inr), "built": date.today().isoformat()}
    (ROOT / "ppt" / "2C_Vinegar_India_White_Vinegar_data.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(path)
    print(f"anchor {anchor_t:,.0f} t | sized {sized_t:,.0f} t ({sized_t / anchor_t:.0%}) | sized ₹{inr_cr(sized_inr):,.1f} cr")
    for r in rows:
        print(f"  {r['name']:28} host {r['host_t']:>12,.0f} t  vinegar {r['vinegar_t']:>9,.0f} t  ₹{inr_cr(r['inr']):>7,.1f} cr  US${r['usd_mn']:.2f} Mn")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
