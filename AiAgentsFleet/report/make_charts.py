"""Generates every chart PNG for one market's whitepaper, in the same visual
style (colors, fonts, chart types) as `vitamin_c_market_whitepaper_v2.docx`'s
19 embedded figures -- colors were sampled directly from that template's
images via PIL (see palette below).

Usage:
    python make_charts.py --market-slug super-intensive-shrimp-farming-market

Reads:  AiAgentsFleet/output/<slug>/whitepaper_fields.json
Writes: AiAgentsFleet/output/<slug>/charts/*.png
"""
from __future__ import annotations

import argparse
import json
import math
import textwrap
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import labels as dim_labels
from narrative import ordered_regions

ROOT = Path(__file__).resolve().parent.parent  # AiAgentsFleet/
OUTPUT_ROOT = ROOT / "output"

# Palette sampled from the Vitamin C template's own embedded chart images.
NAVY = "#0A2540"
TEAL = "#00B4A6"
ORANGE = "#F59E0B"
PURPLE = "#8B5CF6"
GREEN = "#10B981"
GRAY = "#9CA3AF"
RED = "#DC2626"
PALETTE = [NAVY, TEAL, ORANGE, PURPLE, GREEN, GRAY]

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.size": 13,
    "axes.edgecolor": "#D0D5DD",
    "axes.labelcolor": "#334155",
    "text.color": "#0A2540",
    "xtick.color": "#475569",
    "ytick.color": "#475569",
    "axes.grid": True,
    "grid.color": "#EEF1F5",
    "grid.linewidth": 0.8,
    "axes.axisbelow": True,
    "figure.facecolor": "white",
    "savefig.facecolor": "white",
})


def _save(fig, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)


def _strip_spines(ax) -> None:
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)


def _fmt_num(v: float, decimals: int = 0) -> str:
    """Whole numbers with thousands separators, B/M suffix for large USD-Mn
    values -- e.g. 11252.0 -> "11.3B", 4638.0 -> "4,638", never "4,638.00"."""
    if v is None:
        return "n/a"
    if abs(v) >= 1000:
        return f"{v / 1000:,.{max(decimals, 1)}f}B"
    return f"{v:,.{decimals}f}"


def _wrap(label: str, width: int = 12) -> str:
    return "\n".join(textwrap.wrap(str(label), width=width)) or str(label)


def _usd_short(v_mn: float) -> str:
    """Template-style centre/axis label: USD 2.24B / USD 297M."""
    v_mn = float(v_mn or 0)
    return f"USD {v_mn / 1000:,.2f}B" if v_mn >= 1000 else f"USD {v_mn:,.0f}M"


def _short_name(label: str, width: int = 34) -> str:
    """Drops a trailing parenthetical and truncates very long names so axis
    labels stay readable ("Price-sensitive bulk buyers (commodity ...)" ->
    "Price-sensitive bulk buyers")."""
    label = str(label).split(" (")[0].strip()
    return label if len(label) <= width else label[: width - 1].rstrip() + "\u2026"


def _in_billions(values: list[float]) -> bool:
    return bool(values) and max(values) >= 1000


def chart_market_size(field: dict, out: Path) -> None:
    # history is plotted only when consistency's history_check passed
    hist = (field.get("historical_usd_mn", {}) or {}) if (field.get("history_check") or {}).get("ok") else {}
    fc = field.get("forecast_series_usd_mn", {}) or {}
    hyears = sorted(int(y) for y in hist)
    fyears = sorted(int(y) for y in fc)
    fig, ax = plt.subplots(figsize=(10, 5.5))
    bn = _in_billions(list(hist.values()) + list(fc.values()))
    div = 1000.0 if bn else 1.0
    hist = {k: v / div for k, v in hist.items()}
    fc = {k: v / div for k, v in fc.items()}

    def _lbl(v):
        return f"{v:,.2f}" if bn else f"{v:,.0f}"
    if hyears:
        hv = [hist[str(y)] for y in hyears]
        ax.plot(hyears, hv, "-o", color=NAVY, linewidth=2.5, label="Historical")
        # Label every other point on dense series to avoid overlap, but
        # always label the first and last.
        label_idx = set(range(0, len(hyears), max(1, len(hyears) // 5))) | {0, len(hyears) - 1}
        for i, (x, y) in enumerate(zip(hyears, hv)):
            if i in label_idx:
                ax.annotate(_lbl(y), (x, y), textcoords="offset points", xytext=(0, 10),
                            ha="center", fontsize=10, fontweight="bold", color=NAVY)
    if fyears:
        fv = [fc[str(y)] for y in fyears]
        ax.plot(fyears, fv, "-o", color=TEAL, linewidth=2.5, label="Forecast (Base Case)")
        ax.fill_between(fyears, fv, min(fv) * 0.9, color=TEAL, alpha=0.12)
        mid = fyears.index(2030) if 2030 in fyears else len(fyears) // 2
        for i in sorted({0, mid, len(fyears) - 1}):
            ax.annotate(_lbl(fv[i]), (fyears[i], fv[i]), textcoords="offset points", xytext=(0, 10),
                        ha="center", fontsize=11, fontweight="bold", color=TEAL)
    if hyears and fyears:
        ax.axvline(hyears[-1] + 0.5, color="#CBD5E1", linestyle=":", linewidth=1)
    ax.set_ylabel(f"Market Size (USD {'Billion' if bn else 'Million'})")
    ax.legend(frameon=False, loc="upper left")
    _strip_spines(ax)
    _save(fig, out)


def chart_drivers_restraints(field: dict, out: Path) -> None:
    drivers = sorted(field.get("drivers", []) or [], key=lambda d: abs(d.get("cagr_contribution_bps", 0)), reverse=True)
    restraints = sorted(field.get("restraints", []) or [], key=lambda d: abs(d.get("cagr_drag_bps", 0)), reverse=True)
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    for ax, items, color, key, title, sign in (
        (axes[0], drivers, TEAL, "cagr_contribution_bps", "Drivers (bps)", 1),
        (axes[1], restraints, RED, "cagr_drag_bps", "Restraints (bps)", -1),
    ):
        names = [_wrap(_short_name(d.get("name", ""), 60), 32) for d in items][::-1]
        vals = [sign * abs(d.get(key, 0)) for d in items][::-1]
        ax.barh(names, vals, color=color)
        for i, v in enumerate(vals):
            ax.text(v, i, f"{v:+.0f}", va="center",
                     ha="left" if v >= 0 else "right", fontweight="bold", fontsize=10)
        ax.set_title(title, fontweight="bold", loc="left")
        ax.axvline(0, color="#CBD5E1", linewidth=1)
        _strip_spines(ax)
        ax.set_xticks([])
    fig.tight_layout()
    _save(fig, out)


def chart_porters(field: dict, out: Path) -> None:
    forces = field.get("forces", {}) or {}
    labels = ["Bargaining Power\nof Suppliers", "Threat of\nNew Entrants", "Competitive\nRivalry",
              "Threat of\nSubstitutes", "Bargaining Power\nof Buyers"]
    keys = ["supplier_power", "threat_of_new_entrants", "competitive_rivalry",
            "threat_of_substitutes", "buyer_power"]
    values = [float((forces.get(k) or {}).get("score_1_5", 3)) for k in keys]
    values_closed = values + values[:1]
    angles = [n / len(keys) * 2 * math.pi for n in range(len(keys))] + [0]

    fig = plt.figure(figsize=(7, 7))
    ax = fig.add_subplot(111, polar=True)
    ax.set_theta_offset(math.pi / 2)
    ax.set_theta_direction(-1)
    ax.plot(angles, values_closed, "-o", color=TEAL, linewidth=2.5)
    ax.fill(angles, values_closed, color=TEAL, alpha=0.25)
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(labels, fontsize=11, fontweight="bold", color=NAVY)
    ax.set_ylim(0, 5)
    ax.set_yticks([1, 2, 3, 4, 5])
    ax.set_yticklabels(["1", "2", "3", "4", "5"], color="#94A3B8")
    for ang, val in zip(angles[:-1], values):
        ax.annotate(f"{round(val, 1):g}", (ang, val), textcoords="offset points", xytext=(6, 6),
                     fontweight="bold", fontsize=12, color=NAVY)
    _save(fig, out)


def _donut(ax, labels: list[str], values: list[float], center_title: str, center_sub: str) -> None:
    pairs = sorted(zip(labels, values), key=lambda lv: lv[1], reverse=True)
    labels = [_short_name(lb, 40) for lb, _ in pairs]
    values = [v for _, v in pairs]
    colors = (PALETTE * 3)[: len(labels)]
    wedges, _ = ax.pie(values, colors=colors, startangle=90, counterclock=False,
                        wedgeprops={"width": 0.42, "edgecolor": "white", "linewidth": 2})
    ax.text(0, 0.02, center_title, ha="center", va="bottom", fontsize=15, fontweight="bold", color=NAVY)
    ax.text(0, -0.12, _wrap(center_sub, 18), ha="center", va="top", fontsize=10, color="#64748B")
    ax.legend(wedges, [f"{l}  ({v:.1f}%)" for l, v in zip(labels, values)],
               loc="center left", bbox_to_anchor=(1.05, 0.5), frameon=False, fontsize=11)
    ax.set_aspect("equal")


def chart_segmentation(field: dict, out_grade: Path, out_form: Path, market: str,
                       base_val: float = 0, base_year: int = 2025,
                       grade_label: str = "Type", form_label: str = "Form") -> None:
    grade = field.get("by_grade_or_type", []) or []
    form = field.get("by_form", []) or []
    yy = f"{base_year}E"
    if grade:
        fig, ax = plt.subplots(figsize=(7.5, 4.5))
        _donut(ax, [g.get("name", "") for g in grade], [g.get("share_pct_of_market", 0) for g in grade],
               _usd_short(base_val) if base_val else "100%", f"by {grade_label} ({yy})")
        _save(fig, out_grade)
    if form:
        fig, ax = plt.subplots(figsize=(7.5, 4.5))
        _donut(ax, [f.get("name", "") for f in form], [f.get("share_pct_of_market", 0) for f in form],
               _usd_short(base_val) if base_val else "100%", f"by {form_label} ({yy})")
        _save(fig, out_form)


def chart_end_use(field: dict, out_share: Path, out_cagr: Path, base_val: float = 0) -> None:
    apps = sorted(field.get("applications", []) or [], key=lambda a: a.get("share_pct_of_market", 0), reverse=True)
    if not apps:
        return
    names = [a.get("name", "") for a in apps]
    shares = [a.get("share_pct_of_market", 0) for a in apps]
    cagrs = [a.get("cagr_2025_2030_pct", 0) for a in apps]

    fig, ax = plt.subplots(figsize=(11, 3.6))
    scale = (base_val / 100.0) if base_val else 1.0
    left = 0.0
    handles = []
    for name, share, color in zip(names, shares, (PALETTE * 3)):
        w = share * scale
        handles.append(ax.barh([0], [w], left=left, color=color, height=0.6, label=f"{_short_name(name, 40)} ({share:.1f}%)"))
        if share >= 12:
            ax.text(left + w / 2, 0, f"{_wrap(_short_name(name, 30), 16)}\n{share:.1f}%", ha="center", va="center",
                     color="white", fontsize=9, fontweight="bold")
        left += w
    ax.set_xlim(0, left)
    ax.set_yticks([])
    ax.set_xlabel("Market Size (USD Million)" if base_val else "Share of Market Value (%)")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.32), ncol=3, frameon=False, fontsize=9)
    _strip_spines(ax)
    _save(fig, out_share)

    fig, ax = plt.subplots(figsize=(10, 4.5))
    top = max(cagrs) if cagrs else 1
    colors = [ORANGE if c == top else NAVY for c in cagrs]
    bars = ax.bar(range(len(names)), cagrs, color=colors)
    for b, c in zip(bars, cagrs):
        ax.text(b.get_x() + b.get_width() / 2, c, f"{c:.1f}%", ha="center", va="bottom", fontweight="bold")
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels([_wrap(_short_name(nm, 36), 14) for nm in names], fontsize=10)
    ax.set_ylabel("CAGR 2025-2030 (%)")
    _strip_spines(ax)
    _save(fig, out_cagr)


def chart_sub_segments(field: dict, out: Path) -> None:
    groups = field.get("by_top_application", []) or []
    if not groups:
        return
    n = len(groups)
    fig, axes = plt.subplots(1, n, figsize=(5.5 * n, 5))
    if n == 1:
        axes = [axes]
    for ax, grp, color in zip(axes, groups, PALETTE):
        subs = grp.get("sub_segments", []) or []
        names = [_wrap(_short_name(s.get("name", ""), 44), 24) for s in subs][::-1]
        cagrs = [s.get("cagr_pct", 0) for s in subs][::-1]
        vals = [s.get("value_usd_mn") for s in subs][::-1]
        bars = ax.barh(names, cagrs, color=color)
        for b, v, val in zip(bars, cagrs, vals):
            label = f"{v:.1f}%" + (f" ({val:,.0f}M)" if val else "")
            ax.text(v, b.get_y() + b.get_height() / 2, "  " + label, va="center", fontweight="bold", fontsize=9)
        ax.set_title(_wrap(_short_name(grp.get("application", ""), 50), 30), fontweight="bold", loc="left", fontsize=12)
        ax.set_xlabel("CAGR (%)")
        _strip_spines(ax)
    fig.tight_layout()
    _save(fig, out)


def chart_derivatives(field: dict, out: Path) -> None:
    items = sorted(field.get("derivatives", []) or [], key=lambda d: d.get("value_usd_mn", 0), reverse=True)
    if not items:
        return
    names = [d.get("name", "") for d in items][::-1]
    vals = [d.get("value_usd_mn", 0) for d in items][::-1]
    fig, ax = plt.subplots(figsize=(9, 0.6 * len(items) + 1))
    bars = ax.barh(names, vals, color=(PALETTE * 3)[: len(items)])
    for b, v in zip(bars, vals):
        ax.text(v, b.get_y() + b.get_height() / 2, "  " + _usd_short(v), va="center", fontweight="bold")
    _strip_spines(ax)
    ax.set_xticks([])
    _save(fig, out)


def chart_regional_overview(field: dict, out: Path) -> None:
    regions = field.get("regions", {}) or {}
    order = ordered_regions(regions)
    vals = [regions[r].get("value_usd_mn", 0) for r in order]
    shares = [regions[r].get("share_pct_of_global", 0) for r in order]
    cagrs = [regions[r].get("cagr_2025_2030_pct", 0) for r in order]

    fig, ax1 = plt.subplots(figsize=(10, 6))
    bars = ax1.bar(order, vals, color=[NAVY, TEAL, ORANGE, PURPLE, GREEN][: len(order)])
    for b, v, s in zip(bars, vals, shares):
        ax1.text(b.get_x() + b.get_width() / 2, v, f"{_fmt_num(v)}\n({s:.1f}%)", ha="center", va="bottom",
                  fontweight="bold", fontsize=10)
    ax1.set_ylabel("USD Million (base year)")
    ax1.set_xticks(range(len(order)))
    ax1.set_xticklabels([_wrap(r, 10) for r in order])
    _strip_spines(ax1)

    ax2 = ax1.twinx()
    ax2.plot(order, cagrs, "-D", color=RED, linewidth=2, markersize=7)
    # Alternate the CAGR label above/below its point so it never collides
    # with the bar-value label sitting at the top of the (independently
    # scaled) bar directly behind it.
    for i, (x, c) in enumerate(zip(order, cagrs)):
        offset = (0, -16) if i % 2 else (0, 10)
        ax2.annotate(f"{c:.1f}%", (x, c), textcoords="offset points", xytext=offset,
                      ha="center", color=RED, fontweight="bold")
    ax2.set_ylim(min(cagrs) - 2, max(cagrs) + 2)
    ax2.set_ylabel("CAGR 2025-2030 (%)", color=RED)
    ax2.spines["right"].set_visible(True)
    ax2.spines["top"].set_visible(False)
    fig.tight_layout()
    _save(fig, out)


def chart_region_detail(region_name: str, region_data: dict, out: Path, mix_label: str = "Application") -> None:
    countries = region_data.get("countries", []) or []
    mix = region_data.get("application_mix_pct", []) or []
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    if countries:
        names = [c.get("name", "") for c in countries][::-1]
        vals = [c.get("value_usd_mn", 0) for c in countries][::-1]
        bars = axes[0].barh(names, vals, color=NAVY)
        for b, v in zip(bars, vals):
            axes[0].text(v, b.get_y() + b.get_height() / 2, f"  {v:,.1f}", va="center", fontweight="bold", fontsize=9)
        axes[0].set_title("Market size by country (USD M)", fontweight="bold", loc="left", fontsize=11)
        _strip_spines(axes[0])
        axes[0].set_xticks([])
    if mix:
        _donut(axes[1], [m.get("name", "") for m in mix], [m.get("share_pct", 0) for m in mix],
               _wrap(region_name, 14), f"{mix_label} mix")
    fig.tight_layout()
    _save(fig, out)


def chart_competitive(field: dict, out: Path, base_val: float = 0) -> None:
    companies = field.get("companies", []) or []
    others = field.get("others_share_pct", 0)
    names = [c.get("name", "") for c in companies] + (["Others"] if others else [])
    shares = [c.get("share_pct", 0) for c in companies] + ([others] if others else [])
    if not names:
        return
    colors = (["#0A2540", "#1E3A8A", "#2563EB", "#3B82F6", "#00B4A6", "#0D9488", "#14B8A6",
                "#5EEAD4", "#F59E0B", "#FBBF24"] + [GRAY])[: len(names)]
    fig, ax = plt.subplots(figsize=(9, 6))
    wedges, _ = ax.pie(shares, colors=colors, startangle=90, counterclock=False,
                        wedgeprops={"width": 0.4, "edgecolor": "white", "linewidth": 2})
    total = sum(c.get("value_usd_mn", 0) for c in companies) or None
    if base_val:
        ax.text(0, 0.1, _usd_short(base_val), ha="center", va="center", fontsize=15, fontweight="bold", color=NAVY)
        ax.text(0, -0.12, "by Company", ha="center", va="center", fontsize=10, color="#64748B")
    else:
        ax.text(0, 0, "By Company", ha="center", va="center", fontsize=14, fontweight="bold", color=NAVY)
    ax.legend(wedges, [f"{n} ({s:.1f}%)" for n, s in zip(names, shares)],
               loc="center left", bbox_to_anchor=(1.05, 0.5), frameon=False, fontsize=10)
    ax.set_aspect("equal")
    _save(fig, out)


def chart_regulation(field: dict, out: Path) -> None:
    areas = sorted(field.get("compliance_areas", []) or [],
                    key=lambda a: a.get("cost_pct_of_cogs_high", 0), reverse=True)
    if not areas:
        return
    names = [_short_name(a.get("name", ""), 48) for a in areas][::-1]
    los = [a.get("cost_pct_of_cogs_low", 0) for a in areas][::-1]
    his = [a.get("cost_pct_of_cogs_high", 0) for a in areas][::-1]
    fig, ax = plt.subplots(figsize=(9, 0.55 * len(areas) + 1))
    bars = ax.barh(names, his, color=NAVY)
    for b, lo, hi in zip(bars, los, his):
        fmt = (lambda v: f"{v:.1f}") if hi < 10 else (lambda v: f"{v:.0f}")
        ax.text(hi, b.get_y() + b.get_height() / 2, f"  {fmt(lo)}-{fmt(hi)}%", va="center", fontweight="bold")
    ax.set_xlabel("Compliance Cost (% of COGS)")
    _strip_spines(ax)
    _save(fig, out)


def chart_wtp(field: dict, out: Path) -> None:
    segs = [s for s in field.get("consumer_segments", []) or [] if (s.get("baseline_usd") or 0) > 0]
    if not segs:
        return
    labels = ["Baseline", "+ Proven performance", "+ Premium positioning", "+ Top-tier format"]
    keys = ["baseline_usd", "with_clinical_evidence_usd", "with_premium_positioning_usd", "with_top_tier_format_usd"]
    names = [s.get("name", "") for s in segs]
    x = np.arange(len(names))
    width = 0.2
    fig, ax = plt.subplots(figsize=(11, 5.5))
    for i, (label, key, color) in enumerate(zip(labels, keys, PALETTE)):
        vals = [s.get(key, 0) for s in segs]
        ax.bar(x + (i - 1.5) * width, vals, width, label=label, color=color)
    ax.set_xticks(x)
    ax.set_xticklabels([_wrap(_short_name(n, 40), 16) for n in names], fontsize=10)
    ax.set_ylabel(_wrap(f"Willingness to Pay ({field.get('unit') or 'USD'})", 34))
    ax.legend(frameon=False, loc="upper left")
    _strip_spines(ax)
    _save(fig, out)


def chart_scenarios(market_size: dict, scenarios: dict, out: Path) -> None:
    fc = market_size.get("forecast_series_usd_mn", {}) or {}
    years = sorted(int(y) for y in fc)
    if not years:
        return
    base0 = fc[str(years[0])]
    paths = {}
    for name, color in (("bear", RED), ("base", NAVY), ("bull", TEAL)):
        s = scenarios.get(name, {}) or {}
        cagr = (s.get("cagr_pct", 0) or 0) / 100.0
        paths[name] = ([base0 * ((1 + cagr) ** (y - years[0])) for y in years], color, s.get("cagr_pct", 0))

    bn = base0 >= 1000
    div = 1000.0 if bn else 1.0
    fig, ax = plt.subplots(figsize=(11, 6))
    for name, (series, color, cagr) in paths.items():
        series = [v / div for v in series]
        ax.plot(years, series, "-o", color=color, linewidth=2.5, label=f"{name.title()} ({cagr:+.1f}%)")
        ax.annotate(f"{series[-1]:,.2f}B" if bn else f"{series[-1]:,.0f}M", (years[-1], series[-1]),
                     textcoords="offset points", xytext=(6, 0), fontweight="bold", color=color)
    ax.fill_between(years, [v / div for v in paths["bear"][0]], [v / div for v in paths["bull"][0]],
                    color=TEAL, alpha=0.08)
    ax.set_ylabel(f"Market Size (USD {'Billion' if bn else 'Million'})")
    ax.legend(frameon=False, loc="upper left")
    _strip_spines(ax)
    _save(fig, out)


def generate_all(slug: str) -> Path:
    market_dir = OUTPUT_ROOT / slug
    data = json.loads((market_dir / "whitepaper_fields.json").read_text(encoding="utf-8"))
    fields = data["fields"]
    market = data["market_name"]
    charts_dir = market_dir / "charts"
    # Same dimension names as the headings/captions (from prose.json, written first).
    lab = dim_labels.load(market_dir, fields, market)

    if "market_size" in fields:
        chart_market_size(fields["market_size"], charts_dir / "fig1_1_market_size.png")
    if "drivers_restraints" in fields:
        chart_drivers_restraints(fields["drivers_restraints"], charts_dir / "fig2_1_drivers_restraints.png")
    if "porters_five_forces" in fields:
        chart_porters(fields["porters_five_forces"], charts_dir / "fig3_1_porters.png")
    if "segmentation_grade_form" in fields:
        ms = fields.get("market_size") or {}
        chart_segmentation(fields["segmentation_grade_form"], charts_dir / "fig4_1_grade.png",
                             charts_dir / "fig4_2_form.png", market,
                             ms.get("base_year_value_usd_mn", 0), ms.get("base_year", 2025),
                             lab["grade"]["singular"], lab["form"]["singular"])
    if "end_use_industry" in fields:
        chart_end_use(fields["end_use_industry"], charts_dir / "fig5_1_end_use_share.png",
                       charts_dir / "fig5_2_end_use_cagr.png",
                       (fields.get("market_size") or {}).get("base_year_value_usd_mn", 0))
    if "sub_segments" in fields:
        chart_sub_segments(fields["sub_segments"], charts_dir / "fig6_1_sub_segments.png")
    # A chart whose data is now empty must not linger from an earlier run --
    # the docx builder embeds whatever PNG exists at the path.
    (charts_dir / "fig7_1_derivatives.png").unlink(missing_ok=True)
    if "derivatives_pricing" in fields:
        chart_derivatives(fields["derivatives_pricing"], charts_dir / "fig7_1_derivatives.png")
    if "regional" in fields:
        chart_regional_overview(fields["regional"], charts_dir / "fig8_1_regional.png")
        regions = fields["regional"].get("regions", {}) or {}
        for i, rname in enumerate(ordered_regions(regions), start=1):
            chart_region_detail(rname, regions[rname], charts_dir / f"fig{8 + i}_1_region_{i}.png",
                                lab["region_mix"]["singular"])
    if "competitive_landscape" in fields:
        chart_competitive(fields["competitive_landscape"], charts_dir / "fig14_1_competitive.png",
                          (fields.get("market_size") or {}).get("base_year_value_usd_mn", 0))
    if "regulation" in fields:
        chart_regulation(fields["regulation"], charts_dir / "fig15_1_regulation.png")
    if "willingness_to_pay" in fields:
        chart_wtp(fields["willingness_to_pay"], charts_dir / "fig16_1_wtp.png")
    if "market_size" in fields and "scenarios" in fields:
        chart_scenarios(fields["market_size"], fields["scenarios"], charts_dir / "fig18_1_scenarios.png")

    return charts_dir


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--market-slug", required=True)
    args = p.parse_args()
    out = generate_all(args.market_slug)
    print(f"Wrote charts to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
