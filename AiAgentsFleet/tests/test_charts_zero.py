"""A geography or segment set that totals zero (e.g. a zero-value 'Rest of' unit) gets no chart:
the chart function returns None instead of dividing by zero, and the builder leaves the
figure out cleanly (no caption, no image, no source line, figure numbers stay consecutive)."""
from pathlib import Path

import pytest

from AiAgentsFleet.cmi_report import build, charts as ch, model

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "templates" / "cmi_whitepaper_template.docx"
N = model.node

# a zero-value 'Rest of Middle East': every segment is 0 in both years (old pre-PLAN-03 data)
REST_ZERO = {"Hardware": N(0.0, 0.0), "Software": N(0.0, 0.0), "Services": N(0.0, 0.0)}
SAUDI = {"Hardware": N(10.0, 22.0), "Software": N(4.0, 11.0), "Services": N(1.0, 2.0)}


def test_type_chart_skips_zero_rest_of_unit(tmp_path):
    out = tmp_path / "type_rest_of_middle_east.png"
    assert ch.type_chart(REST_ZERO, "Product Type", out) is None
    assert not out.exists()
    assert ch.type_chart(SAUDI, "Product Type", tmp_path / "type_saudi_arabia.png").exists()


def test_demand_chart_skips_zero_rest_of_unit(tmp_path):
    out = tmp_path / "demand_rest_of_middle_east.png"
    assert ch.demand_chart(REST_ZERO, out) is None
    assert not out.exists()
    assert ch.demand_chart(SAUDI, tmp_path / "demand_saudi_arabia.png").exists()


def test_region_donuts_skip_zero_total(tmp_path):
    regs = ["North America", "Asia Pacific", "Europe", "Latin America", "Middle East & Africa"]
    # one base-year (2025A) doughnut: market shares are a single-year pie (user decision 2026-10-18)
    assert ch.region_donuts({"regions": {r: {**N(0.0, 0.0), "v25": 0.0} for r in regs}}, tmp_path / "d.png") is None
    m = {"regions": {r: {**N(10.0 + k, 20.0 + k), "v25": 9.0 + k} for k, r in enumerate(regs)}}
    assert ch.region_donuts(m, tmp_path / "ok.png").exists()


def test_other_charts_skip_zero_totals(tmp_path):
    zero = {"Rest of Middle East": N(0.0, 0.0), "Kuwait": N(0.0, 0.0)}
    colors = {k: "#123456" for k in zero}
    assert ch.bubble(zero, colors, tmp_path / "b.png") is None
    assert ch.stacked_years({k: {2025: 0.0, 2026: 0.0} for k in zero}, colors, tmp_path / "s.png") is None
    assert ch.opportunity_bars(list(zero.items()), colors, "t", tmp_path / "o.png") is None
    assert ch.vendor_donut([("Acme", 0.0), ("Regional & other", 0.0)], "MEA", tmp_path / "v.png", 2025) is None
    # one zero 'Rest of' unit among real countries still gets its bubble chart
    mixed = {"Saudi Arabia": N(15.0, 35.0), "Rest of Middle East": N(0.0, 0.0)}
    assert ch.bubble(mixed, {k: "#123456" for k in mixed}, tmp_path / "mixed.png").exists()


@pytest.mark.skipif(not TEMPLATE.exists(), reason="CMI template not present")
def test_builder_leaves_skipped_figure_out_cleanly(tmp_path):
    b = build.Builder(TEMPLATE)
    # template profile block: 171 heading, 172 caption, 173 image, 174 source, 175 paragraph
    b.para(171, "Rest of Middle East Opportunity by Product Type")
    b.caption(172, "Figure", "Rest of Middle East market by product type, 2026E vs 2033F")
    b.image(173, ch.type_chart(REST_ZERO, "Product Type", tmp_path / "type_rest.png"))
    b.keep(174)
    b.para(175, "Rest of Middle East has no measurable product-type split.")
    texts = [e.xpath("string(.)") for e in b.out]
    assert texts == ["Rest of Middle East Opportunity by Product Type",
                     "Rest of Middle East has no measurable product-type split."]
    assert b.fig == 0
    assert any(x.startswith("figure left out (no data to chart): Rest of Middle East market") for x in b.log)
    # the next figure keeps consecutive numbering, and its source line is kept
    b.caption(172, "Figure", "Saudi Arabia market by product type, 2026E vs 2033F")
    b.image(173, ch.type_chart(SAUDI, "Product Type", tmp_path / "type_saudi.png"))
    b.keep(174)
    texts = [e.xpath("string(.)") for e in b.out]
    assert texts[2].startswith("Figure 1: Saudi Arabia") and texts[-1].startswith("Source")
