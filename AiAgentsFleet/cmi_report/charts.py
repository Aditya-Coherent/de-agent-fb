"""Every figure of the CMI whitepaper template, drawn from the numeric model
(model.py) in the template's own style: navy / teal / steel-blue / orange
palette, Calibri, light panels, direct value labels. Labels use fmt.py, the
same formatter as the tables and text, so a chart never shows a number the
text doesn't.

Nothing here is market-specific: names, dimensions and values all come from
the model and the agents' fields.
"""
from __future__ import annotations

import math
import textwrap
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, FancyBboxPatch, Polygon, Rectangle

from AiAgentsFleet.cmi_report import fmt
from AiAgentsFleet.cmi_report.model import Y0, Y1

NAVY, TEAL, STEEL, LIGHT = "#0D2442", "#14A89A", "#3C7BA6", "#8DB8DD"
ORANGE, RED, PURPLE, GRAY = "#F2A541", "#D9475A", "#6B45B8", "#C9D1DB"
INK, MUTED, PANEL = "#0D2442", "#6B7785", "#EEF2F6"
CATEGORICAL = [NAVY, TEAL, STEEL, ORANGE, RED, PURPLE, LIGHT]
REGION_COLORS = {"North America": NAVY, "Asia Pacific": STEEL, "Europe": LIGHT,
                 "Latin America": ORANGE, "Middle East & Africa": TEAL}
DPI = 200
# Width of data charts (inches). Figures are placed ~6 in wide in the page, so a
# narrower canvas keeps chart text readable; card-style figures use CW.
W, CW = 8.6, 10.4

plt.rcParams.update({
    "font.family": "Calibri", "font.size": 12, "text.color": INK, "axes.labelcolor": INK,
    "axes.edgecolor": INK, "xtick.color": INK, "ytick.color": INK, "axes.grid": False,
    "axes.spines.top": False, "axes.spines.right": False, "figure.facecolor": "white",
    "savefig.facecolor": "white",
})


def _save(fig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=DPI, bbox_inches="tight", pad_inches=0.08)
    plt.close(fig)
    return path


def _wrap(s: str, w: int) -> str:
    return "\n".join(textwrap.wrap(str(s), w)) or str(s)


def _bn(mn: float) -> float:
    return mn / 1000


def _no_data(values) -> bool:
    """Nothing to draw: the values total zero (e.g. a 'Rest of' unit with no market). The chart is
    skipped (the function returns None) and the builder leaves the figure out."""
    return not values or sum(max(0.0, float(v)) for v in values) <= 0


def _axis_unit(values: list[float]) -> tuple[float, str, str]:
    """(divisor, axis label, total-label format) for a USD-million series."""
    if max(values) >= 1000:
        return 1000.0, "USD billion", ("{:.0f}" if max(values) >= 20_000 else "{:.1f}")
    return 1.0, "USD million", "{:.0f}"


# --------------------------------------------------------------------------- #
# cover and headline panels
# --------------------------------------------------------------------------- #
def cover(m: dict, path: Path) -> Path:
    fig = plt.figure(figsize=(6.6, 5.1))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 132)
    ax.set_ylim(0, 102)
    ax.axis("off")
    ax.add_patch(Rectangle((2, 0), 128, 100, color=NAVY))
    rng = __import__("random").Random(7)
    for _ in range(60):
        ax.add_patch(Circle((rng.uniform(4, 128), rng.uniform(4, 98)), rng.uniform(0.15, 0.6),
                            color="#2B4A7A", alpha=0.8))
    cx, cy = 93, 57
    for r in (18, 27, 37):
        ax.add_patch(Circle((cx, cy), r, fill=False, ec=TEAL, lw=1.0, alpha=0.55))
    # market-neutral motif: rising bars with a growth arrow
    for i, h in enumerate((9, 15, 22)):
        ax.add_patch(FancyBboxPatch((cx - 11 + i * 8, cy - 10), 5.2, h, boxstyle="round,pad=0,rounding_size=1.2",
                                    color="white" if i < 2 else TEAL, ec="none"))
    ax.plot([cx - 12, cx - 3, cx + 3, cx + 13], [cy + 2, cy + 8, cy + 6, cy + 16], color=ORANGE, lw=2.4,
            solid_capstyle="round")
    ax.add_patch(Polygon([[cx + 13.8, cy + 17.2], [cx + 9.6, cy + 16.2], [cx + 12.6, cy + 12.8]], color=ORANGE))
    xs = [8 + i for i in range(0, 56)]
    ax.plot(xs, [30 + 7 * math.sin((x - 8) / 9) + (x - 8) * 0.12 for x in xs], ls=(0, (4, 3)), color=ORANGE, lw=1.8)
    ax.add_patch(Circle((xs[-1], 30 + 7 * math.sin((xs[-1] - 8) / 9) + (xs[-1] - 8) * 0.12), 1.4, color=ORANGE))
    g, mea = m["global"], m["regions"]["Middle East & Africa"]
    kpis = [(fmt.usd(g["v26"]), f"Global market {Y0}E"), (fmt.pct(g["cagr"]), f"Global CAGR {Y0}-{str(Y1)[2:]}"),
            (fmt.pct(mea["cagr"]), f"MEA CAGR {Y0}-{str(Y1)[2:]}"), (fmt.usd(mea["v33"]), f"MEA market {Y1}F")]
    for i, (big, small) in enumerate(kpis):
        ax.text(7 + i * 30.5, 11.5, big, color=ORANGE, fontsize=17, fontweight="bold")
        ax.text(7 + i * 30.5, 5.5, small, color="white", fontsize=8.5)
    return _save(fig, path)


def kpi_panel(m: dict, path: Path) -> Path:
    g, mea = m["global"], m["regions"]["Middle East & Africa"]
    fig = plt.figure(figsize=(CW, 4.0))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 104)
    ax.set_ylim(0, 40)
    ax.axis("off")
    for row, (label, color, d) in enumerate((("GLOBAL", NAVY, g), ("MIDDLE EAST & AFRICA", TEAL, mea))):
        y = 21 if row == 0 else 1
        ax.add_patch(FancyBboxPatch((2, y), 100, 17, boxstyle="round,pad=0,rounding_size=1", color=PANEL))
        ax.add_patch(Rectangle((2, y), 1.6, 17, color=color))
        ax.text(6.5, y + 13.3, label, fontsize=12, fontweight="bold", color=color)
        for i, (big, small) in enumerate(((fmt.usd(d["v26"]), f"Market size {Y0}E"),
                                          (fmt.usd(d["v33"]), f"Forecast {Y1}"),
                                          (fmt.pct(d["cagr"]), f"CAGR {Y0}-{str(Y1)[2:]}"))):
            ax.text(6.5 + i * 32, y + 5.2, big, fontsize=27, fontweight="bold", color=color)
            ax.text(6.5 + i * 32, y + 1.6, small, fontsize=11.5, color=MUTED)
    return _save(fig, path)


def cagr_and_share(m: dict, path: Path) -> Path:
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(W, 4.2), gridspec_kw={"width_ratios": [1.05, 1]})
    names = ["Global", "Middle East\n& Africa", "Middle East", "Africa"]
    vals = [m["global"]["cagr"], m["regions"]["Middle East & Africa"]["cagr"],
            m["subregions"]["Middle East"]["cagr"], m["subregions"]["Africa"]["cagr"]]
    bars = a1.bar(names, vals, color=[NAVY, TEAL, STEEL, ORANGE], width=0.62)
    for b, v in zip(bars, vals):
        a1.text(b.get_x() + b.get_width() / 2, v, fmt.pct(v), ha="center", va="bottom", fontweight="bold")
    a1.set_yticks([])
    a1.spines["left"].set_visible(False)
    a1.set_title(f"CAGR {Y0}-{Y1}", loc="left", fontweight="bold", fontsize=12)
    years = sorted(m["global"]["series"])
    share = [m["regions"]["Middle East & Africa"]["series"][y] / m["global"]["series"][y] * 100 for y in years]
    a2.plot(years, share, "-o", color=TEAL, lw=2.6, ms=6)
    a2.fill_between(years, share, min(share) - 0.1 * (max(share) - min(share) + 0.1), color=TEAL, alpha=0.12)
    for i in (0, len(years) - 1):
        a2.annotate(fmt.pct(share[i]), (years[i], share[i]), textcoords="offset points", xytext=(0, 12),
                    ha="center", color=TEAL, fontweight="bold")
    span = max(share) - min(share)
    a2.set_ylim(min(share) - max(span, 0.3) * 0.6, max(share) + max(span, 0.3) * 0.6)
    a2.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.1f}%"))
    a2.set_xticks(years[::2])
    a2.set_title("MEA share of global market", loc="left", fontweight="bold", fontsize=12)
    fig.tight_layout()
    return _save(fig, path)


def trend_cards(trends: list[dict], path: Path) -> Path:
    fig = plt.figure(figsize=(CW, 5.0))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 104)
    ax.set_ylim(0, 50)
    ax.axis("off")
    colors = [NAVY, TEAL, STEEL, ORANGE, RED, PURPLE]
    for i, t in enumerate(trends[:6]):
        x, y = 2 + (i % 3) * 34, 26 if i < 3 else 1
        ax.add_patch(FancyBboxPatch((x, y), 32, 23, boxstyle="round,pad=0,rounding_size=1", color=PANEL))
        ax.add_patch(Circle((x + 4.6, y + 17.5), 3.2, color=colors[i]))
        ax.text(x + 4.6, y + 17.5, f"{i + 1:02d}", ha="center", va="center", color="white", fontweight="bold",
                fontsize=13)
        ax.text(x + 9.2, y + 17.5, _wrap(t.get("title", ""), 18), va="center", fontweight="bold", fontsize=14)
        ax.text(x + 2.6, y + 11, _wrap(t.get("tagline", ""), 30), va="top", color="#4A5563", fontsize=12.5)
    return _save(fig, path)


def drivers_restraints(drivers: list[dict], restraints: list[dict], path: Path) -> Path:
    items = sorted(drivers, key=lambda d: -d["impact_pp"]) + sorted(restraints, key=lambda d: d["impact_pp"])
    names = [d["name"] for d in items][::-1]
    vals = [float(d["impact_pp"]) for d in items][::-1]
    fig, ax = plt.subplots(figsize=(W, 0.45 * len(items) + 1.0))
    ax.barh([_wrap(n, 34) for n in names], vals, color=[TEAL if v > 0 else RED for v in vals], height=0.62)
    for i, v in enumerate(vals):
        ax.text(v + (0.05 if v > 0 else -0.05), i, fmt.pp(v), va="center", ha="left" if v > 0 else "right",
                fontweight="bold", fontsize=11)
    ax.axvline(0, color=INK, lw=1)
    ax.spines["left"].set_visible(False)
    ax.spines["bottom"].set_visible(False)
    ax.set_xticks([])
    ax.tick_params(axis="y", length=0)
    lim = max(abs(v) for v in vals) * 1.35
    ax.set_xlim(-lim, lim)
    ax.text(0.99, 0.04, f"Net effect on MEA CAGR\n{fmt.pp(sum(vals))} vs. neutral baseline", transform=ax.transAxes,
            ha="right", va="bottom", color=MUTED, fontsize=9, style="italic")
    return _save(fig, path)


# --------------------------------------------------------------------------- #
# global sizing
# --------------------------------------------------------------------------- #
def stacked_years(rows: dict[str, dict[int, float]], colors: dict[str, str], path: Path,
                  ncol: int = 3) -> Path | None:
    if not rows:
        return None
    years = sorted(next(iter(rows.values())))
    totals = [sum(r[y] for r in rows.values()) for y in years]
    if _no_data(totals):
        return None
    div, label, tfmt = _axis_unit(totals)
    fig, ax = plt.subplots(figsize=(W, 5.0))
    bottom = [0.0] * len(years)
    for name, r in rows.items():
        vals = [r[y] / div for y in years]
        ax.bar(range(len(years)), vals, bottom=bottom, color=colors[name], width=0.62, label=name,
               edgecolor="white", linewidth=0.8)
        bottom = [b + v for b, v in zip(bottom, vals)]
    for i, t in enumerate(totals):
        # same display format as the text and tables (one fact = one number)
        ax.text(i, t / div, fmt.usd(t), ha="center", va="bottom", fontweight="bold", fontsize=9)
    ax.set_xticks(range(len(years)))
    ax.set_xticklabels([fmt.year_label(y) for y in years])
    ax.set_ylabel(label)
    ax.set_ylim(0, max(totals) / div * 1.12)
    ax.legend(frameon=False, ncol=ncol, loc="upper left", fontsize=10)
    return _save(fig, path)


def scenarios(m: dict, path: Path) -> Path:
    sc = m["scenarios"]
    years = list(range(Y0, Y1 + 1))
    allv = [v for s in sc.values() for v in s["series"].values()]
    div, label, _ = _axis_unit(allv)
    fig, ax = plt.subplots(figsize=(W, 5.0))
    style = {"Optimistic": (TEAL, "--"), "Base": (NAVY, "-"), "Conservative": ("#6E7F95", "--")}
    for name in ("Optimistic", "Base", "Conservative"):
        s = sc[name]
        vals = [s["series"][y] / div for y in years]
        col, ls = style[name]
        ax.plot(years, vals, ls, marker="o", color=col, lw=2.4, ms=5)
        ax.text(years[-1] + 0.12, vals[-1], f"{name}\n{fmt.usd(s['series'][Y1])} ({fmt.pct(s['cagr'])} CAGR)",
                color=col, fontweight="bold", fontsize=9.5, va="center")
    lo = [sc["Conservative"]["series"][y] / div for y in years]
    hi = [sc["Optimistic"]["series"][y] / div for y in years]
    ax.fill_between(years, lo, hi, color=STEEL, alpha=0.1)
    ax.set_xlim(years[0] - 0.2, years[-1] + 1.6)
    ax.set_ylabel(label)
    return _save(fig, path)


def type_chart(seg: dict, dim: str, path: Path) -> Path | None:
    """Stacked 2026E vs 2033F by segment + CAGR bars (template 'by drone type'). None when the
    segments total zero."""
    names = sorted(seg, key=lambda n: -seg[n]["v33"])
    cols = {n: CATEGORICAL[i % len(CATEGORICAL)] for i, n in enumerate(names)}
    tot26, tot33 = sum(seg[n]["v26"] for n in names), sum(seg[n]["v33"] for n in names)
    if _no_data([seg[n]["v33"] for n in names]):
        return None
    unit = fmt.unit_for([tot26])
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(W, 4.6), gridspec_kw={"width_ratios": [1, 1.15]})
    for x, key, tot in ((0, "v26", tot26), (1, "v33", tot33)):
        b = 0.0
        for n in names[::-1]:
            v = seg[n][key]
            a1.bar(x, v, bottom=b, color=cols[n], width=0.58, edgecolor="white", linewidth=0.8)
            if v / tot33 > 0.06:
                a1.text(x, b + v / 2, fmt.usd(v), ha="center", va="center", color="white", fontsize=8.5,
                        fontweight="bold")
            b += v
    a1.set_xticks([0, 1])
    a1.set_xticklabels([f"{Y0}E", f"{Y1}F"])
    a1.set_yticks([])
    a1.spines["left"].set_visible(False)
    a1.set_ylim(0, tot33 * 1.38)
    a1.set_title(f"Market value by {fmt_dim(dim)}", loc="left", fontweight="bold", fontsize=11.5)
    a1.legend(handles=[Rectangle((0, 0), 1, 1, color=cols[n]) for n in names], labels=[_wrap(n, 30) for n in names],
              frameon=False, fontsize=8.5, loc="upper left")
    cg = [seg[n]["cagr"] for n in names]
    a2.barh(range(len(names)), cg, color=[cols[n] for n in names], height=0.62)
    for i, v in enumerate(cg):
        a2.text(v, i, "  " + fmt.pct(v), va="center", fontweight="bold", fontsize=9.5)
    a2.set_yticks(range(len(names)))
    a2.set_yticklabels([_wrap(n, 22) for n in names], fontsize=9)
    a2.invert_yaxis()
    a2.set_xticks([])
    a2.spines["bottom"].set_visible(False)
    a2.set_xlim(0, max(max(cg), 0.1) * 1.3)
    a2.set_title(f"CAGR {Y0}-{str(Y1)[2:]}", loc="left", fontweight="bold", fontsize=11.5)
    fig.tight_layout()
    return _save(fig, path)


def fmt_dim(dim: str) -> str:
    """'Product Type' -> 'product type' (acronyms kept)."""
    return " ".join(w if w.isupper() and len(w) > 1 else w.lower() for w in dim.split())


def demand_chart(seg: dict, path: Path) -> Path | None:
    """2026E vs 2033F paired bars with the increment (template 'by end user'). None when the
    segments total zero."""
    names = sorted(seg, key=lambda n: seg[n]["v33"])
    if _no_data([seg[n]["v33"] for n in names]):
        return None
    fig, ax = plt.subplots(figsize=(W, 0.62 * len(names) + 1.0))
    y = range(len(names))
    v26 = [seg[n]["v26"] for n in names]
    v33 = [seg[n]["v33"] for n in names]
    ax.barh([i + 0.2 for i in y], v26, height=0.36, color=LIGHT, label=f"{Y0}E")
    ax.barh([i - 0.2 for i in y], v33, height=0.36, color=NAVY, label=f"{Y1}F")
    for i, n in enumerate(names):
        ax.text(v26[i], i + 0.2, "  " + fmt.usd(v26[i]), va="center", fontsize=8.5, color=MUTED)
        ax.text(v33[i], i - 0.2, f"  {fmt.usd(v33[i])}  ({fmt.incr(v33[i] - v26[i])})", va="center",
                fontsize=8.5, fontweight="bold")
    ax.set_yticks(list(y))
    ax.set_yticklabels([_wrap(n, 30) for n in names], fontsize=9.5)
    ax.set_xticks([])
    ax.spines["bottom"].set_visible(False)
    ax.tick_params(axis="y", length=0)
    ax.set_xlim(0, max(v33) * 1.42)
    ax.legend(frameon=False, loc="lower right", fontsize=9)
    return _save(fig, path)


def region_donuts(m: dict, path: Path) -> Path | None:
    regs = ["North America", "Asia Pacific", "Europe", "Latin America", "Middle East & Africa"]
    if any(_no_data([m["regions"][r][key] for r in regs]) for key in ("v26", "v33")):
        return None
    fig, axes = plt.subplots(1, 2, figsize=(W, 4.6))
    for ax, key, yl in ((axes[0], "v26", f"{Y0}E"), (axes[1], "v33", f"{Y1}F")):
        vals = [m["regions"][r][key] for r in regs]
        tot = sum(vals)
        wedges, _ = ax.pie(vals, colors=[REGION_COLORS[r] for r in regs], startangle=90, counterclock=False,
                           wedgeprops={"width": 0.36, "edgecolor": "white", "linewidth": 2})
        for w, v in zip(wedges, vals):
            ang = math.radians((w.theta1 + w.theta2) / 2)
            if v / tot >= 0.04:
                ax.text(0.82 * math.cos(ang), 0.82 * math.sin(ang), fmt.share(v, tot), ha="center",
                        va="center", color="white", fontsize=8.5, fontweight="bold")
        ax.text(0, 0.06, fmt.usd(tot), ha="center", va="center", fontsize=17, fontweight="bold")
        ax.text(0, -0.16, yl, ha="center", va="center", fontsize=10, color=MUTED)
        ax.set_aspect("equal")
    fig.legend(handles=[Rectangle((0, 0), 1, 1, color=REGION_COLORS[r]) for r in regs], labels=regs,
               loc="lower center", ncol=5, frameon=False, fontsize=9.5)
    fig.subplots_adjust(bottom=0.1)
    return _save(fig, path)


def opportunity_bars(items: list[tuple[str, dict]], colors: dict[str, str], title: str,
                     path: Path) -> Path | None:
    if _no_data([d["incr"] for _, d in items]):
        return None
    items = sorted(items, key=lambda kv: kv[1]["incr"])
    fig, ax = plt.subplots(figsize=(W, 0.6 * len(items) + 1.0))
    vals = [d["incr"] for _, d in items]
    ax.barh([n for n, _ in items], vals, color=[colors.get(n, STEEL) for n, _ in items], height=0.6)
    for i, (n, d) in enumerate(items):
        ax.text(d["incr"], i, f"  {fmt.incr(d['incr'])}  |  CAGR {fmt.pct(d['cagr'])}", va="center",
                fontweight="bold", fontsize=9.5)
    ax.set_xticks([])
    ax.spines["bottom"].set_visible(False)
    ax.tick_params(axis="y", length=0)
    ax.set_xlim(0, max(vals) * 1.45)
    ax.set_title(title, fontweight="bold", fontsize=11.5)
    return _save(fig, path)


def bubble(countries: dict[str, dict], colors: dict[str, str], path: Path) -> Path | None:
    if _no_data([d["v26"] for d in countries.values()]):
        return None
    fig, ax = plt.subplots(figsize=(W, 4.8))
    big = max(d["v26"] for d in countries.values())
    incs = [d["incr"] for d in countries.values()]
    div = 1000.0 if max(incs) >= 1000 else 1.0
    for n, d in countries.items():
        s = 200 + 2400 * d["v26"] / big
        ax.scatter(d["cagr"], d["incr"] / div, s=s, color=colors[n], alpha=0.85, edgecolor="white", linewidth=1.5)
        ax.annotate(n, (d["cagr"], d["incr"] / div), textcoords="offset points",
                    xytext=(0, -math.sqrt(s) / 2 - 9), ha="center", fontsize=9, fontweight="bold")
    cg = [d["cagr"] for d in countries.values()]
    ax.set_xlim(min(cg) - 0.8, max(cg) + 0.8)
    ax.set_ylim(min(0, min(incs) / div) - max(incs) / div * 0.18, max(incs) / div * 1.2)
    ax.set_xlabel(f"CAGR {Y0}-{Y1} (%)")
    ax.set_ylabel(f"Incremental revenue {Y0}-{str(Y1)[2:]} (USD {'B' if div > 1 else 'M'})")
    ax.grid(True, color="#E3E8EE", lw=0.8)
    ax.text(0.99, 0.02, f"Bubble size = {Y0}E market size", transform=ax.transAxes, ha="right", color=MUTED,
            fontsize=8.5, style="italic")
    return _save(fig, path)


# --------------------------------------------------------------------------- #
# regulation, vendors, strategy, methodology
# --------------------------------------------------------------------------- #
def timeline(milestones: list[dict], path: Path) -> Path:
    fig = plt.figure(figsize=(CW, 4.2))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 104)
    ax.set_ylim(0, 42)
    ax.axis("off")
    ax.plot([2, 102], [21, 21], color=NAVY, lw=2.2)
    n = len(milestones)
    cols = [TEAL, RED, STEEL, TEAL, ORANGE, PURPLE]
    for i, ms in enumerate(milestones):
        x = 9 + i * (86 / max(1, n - 1))
        c = cols[i % len(cols)]
        ax.add_patch(Circle((x, 21), 1.2, color=c, zorder=3))
        up = i % 2 == 0
        ax.plot([x, x], [21, 25 if up else 17], color=c, lw=1.2)
        label = f"{ms.get('jurisdiction', '')}: {ms.get('event', '')}" if ms.get("jurisdiction") else ms.get("event", "")
        if ms.get("verified") is False:
            label += " [to verify]"
        ax.text(x, 26.5 if up else 15.5, ms.get("date", ""), ha="center", va="bottom" if up else "top",
                color=c, fontweight="bold", fontsize=13)
        ax.text(x, 30 if up else 12, _wrap(label, 22), ha="center", va="bottom" if up else "top", fontsize=11)
    return _save(fig, path)


def heatmap(rows: list[str], cols: list[str], values: list[list[int]], title: str, path: Path) -> Path:
    from matplotlib.colors import LinearSegmentedColormap
    cmap = LinearSegmentedColormap.from_list("cmi", ["#EAF1F8", "#9DC0DE", STEEL, NAVY])
    fig, ax = plt.subplots(figsize=(W, 0.42 * len(rows) + 1.3))
    ax.imshow(values, cmap=cmap, vmin=1, vmax=5, aspect="auto")
    for i, r in enumerate(values):
        for j, v in enumerate(r):
            ax.text(j, i, str(v), ha="center", va="center", color="white" if v >= 3 else INK, fontweight="bold",
                    fontsize=12)
    ax.set_xticks(range(len(cols)))
    ax.set_xticklabels([_wrap(c, 14) for c in cols], fontsize=10.5, fontweight="bold")
    ax.xaxis.tick_top()
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([_wrap(r, 24) for r in rows], fontsize=11, fontweight="bold")
    ax.tick_params(length=0)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_xticks([x - 0.5 for x in range(1, len(cols))], minor=True)
    ax.set_yticks([y - 0.5 for y in range(1, len(rows))], minor=True)
    ax.grid(which="minor", color="white", lw=2)
    ax.set_title(title, fontweight="bold", fontsize=11, pad=34)
    return _save(fig, path)


def _chevron(ax, x, y, w, h, color, first=False):
    tip = 2.2
    pts = [[x, y], [x + w - tip, y], [x + w, y + h / 2], [x + w - tip, y + h], [x, y + h]]
    if not first:
        pts.append([x + tip, y + h / 2])
    ax.add_patch(Polygon(pts, color=color))


def value_chain(stages: list[dict], path: Path) -> Path:
    stages = stages[:5] + [{"stage": "End users", "examples": [], "share": "Demand pull"}]
    fig = plt.figure(figsize=(CW, 3.9))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 104)
    ax.set_ylim(0, 39)
    ax.axis("off")
    cols = [NAVY, STEEL, TEAL, ORANGE, RED, PURPLE]
    w = 16.4
    for i, s in enumerate(stages):
        x = 2 + i * (w + 0.6)
        _chevron(ax, x, 26, w, 7, cols[i], first=i == 0)
        ax.text(x + w / 2 + 0.6, 29.5, _wrap(s["stage"], 13), ha="center", va="center", color="white",
                fontweight="bold", fontsize=11)
        ax.add_patch(FancyBboxPatch((x + 0.6, 11.5), w - 2.2, 12.5, boxstyle="round,pad=0,rounding_size=0.6",
                                    color=PANEL))
        ex = s.get("examples") or []
        ax.text(x + w / 2 - 0.5, 17.75, "\n".join(_wrap(e, 15) for e in ex[:4]), ha="center", va="center",
                fontsize=9.5, color=INK)
        share = s.get("share") or f"{s['share_low_pct']:.0f}-{s['share_high_pct']:.0f}%"
        ax.text(x + w / 2 - 0.5, 6.5, _wrap(share, 10), ha="center", va="center", color=cols[i], fontweight="bold",
                fontsize=15 if "%" in share else 12)
    ax.text(2, 1, "Indicative share of the value pool by stage (CMI estimate); end users represent demand pull.",
            fontsize=9.5, color=MUTED, style="italic")
    return _save(fig, path)


def vendor_donut(rows: list[tuple[str, float]], center: str, path: Path) -> Path | None:
    """`rows` = the model's folded slices (model.fold), last one 'Regional & other'."""
    if _no_data([s for _, s in rows]):
        return None
    colors = CATEGORICAL[: len(rows) - 1] + [GRAY]
    fig, ax = plt.subplots(figsize=(9.0, 4.6))
    wedges, _ = ax.pie([s for _, s in rows], colors=colors, startangle=90, counterclock=False,
                       wedgeprops={"width": 0.36, "edgecolor": "white", "linewidth": 2})
    ax.text(0, 0.07, center, ha="center", va="center", fontsize=17, fontweight="bold")
    ax.text(0, -0.15, f"{Y0 - 1}E value share (est.)", ha="center", va="center", fontsize=9, color=MUTED)
    ax.legend(wedges, [f"{_wrap(n, 30)}   {fmt.pct(s)}" for n, s in rows], loc="center left",
              bbox_to_anchor=(1.02, 0.5), frameon=False, fontsize=9.5)
    ax.set_aspect("equal")
    return _save(fig, path)


def phases(ph: list[dict], path: Path) -> Path:
    fig = plt.figure(figsize=(CW, 4.6))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 104)
    ax.set_ylim(0, 46)
    ax.axis("off")
    cols = [NAVY, TEAL, ORANGE]
    for i, p in enumerate(ph[:3]):
        x = 2 + i * 33.5
        _chevron(ax, x, 35, 32.8, 10, cols[i], first=i == 0)
        ax.text(x + 3.5, 42, f"PHASE {i + 1}  |  {p.get('window', '')}", color="white", fontsize=10,
                fontweight="bold")
        ax.text(x + 3.5, 37.3, _wrap(p.get("name", ""), 26), color="white", fontsize=13, fontweight="bold")
        ax.add_patch(FancyBboxPatch((x, 1), 30.5, 32, boxstyle="round,pad=0,rounding_size=0.8", color=PANEL))
        for j, a in enumerate((p.get("actions") or [])[:3]):
            ax.add_patch(Circle((x + 2.4, 27 - j * 10), 0.75, color=cols[i]))
            ax.text(x + 4.2, 27 - j * 10, _wrap(a, 27), va="center", fontsize=11)
    return _save(fig, path)


PROCESS = [("Define\nscope", f"Segments, geographies, base year {Y0 - 1}, forecast to {Y1}"),
           ("Secondary\nresearch", "Regulators, company filings, trade data, associations"),
           ("Primary\nresearch", "Manufacturers, distributors, end users and regulators"),
           ("Model &\ntriangulate", "Top-down + bottom-up, scenarios, reconciliation"),
           ("Validate &\npublish", "Expert panel review, QA, periodic data refresh")]


def process(path: Path) -> Path:
    fig = plt.figure(figsize=(CW, 3.9))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 104)
    ax.set_ylim(0, 39)
    ax.axis("off")
    cols = [NAVY, STEEL, TEAL, ORANGE, RED]
    for i, (t, d) in enumerate(PROCESS):
        x = 2 + i * 20.2
        ax.add_patch(FancyBboxPatch((x, 1), 19, 37, boxstyle="round,pad=0,rounding_size=0.8", color=PANEL))
        ax.add_patch(Circle((x + 9.5, 31), 3.6, color=cols[i]))
        ax.text(x + 9.5, 31, str(i + 1), ha="center", va="center", color="white", fontweight="bold", fontsize=16)
        ax.text(x + 9.5, 22, t, ha="center", va="center", fontweight="bold", fontsize=13)
        ax.text(x + 9.5, 10, _wrap(d, 17), ha="center", va="center", color="#4A5563", fontsize=11)
    return _save(fig, path)


def published_vs_cmi(published: list[dict], cmi_2025_mn: float, path: Path) -> Path:
    """Drawn only for a sourced headline (model.py empties `published_2025`
    otherwise); the CMI bar is the sourced base, not a blend of the others."""
    rows = [(p["publisher"], float(p["value_usd_bn"])) for p in published] + [("CMI base", cmi_2025_mn / 1000)]
    fig, ax = plt.subplots(figsize=(W, 0.55 * len(rows) + 1.0))
    ys = list(range(len(rows)))[::-1]
    ax.barh(ys, [v for _, v in rows], color=[LIGHT] * (len(rows) - 1) + [TEAL], height=0.6)
    for y, (_, v) in zip(ys, rows):
        ax.text(v, y, f"  {fmt.usd(v * 1000)}", va="center", fontweight="bold", fontsize=9.5)
    ax.set_yticks(ys)
    ax.set_yticklabels([n for n, _ in rows], fontsize=9.5)
    ax.set_xticks([])
    ax.spines["bottom"].set_visible(False)
    ax.tick_params(axis="y", length=0)
    ax.set_xlim(0, max(v for _, v in rows) * 1.25)
    ax.set_title(f"Published {Y0 - 1} global market estimates (for reference) vs. CMI base", fontweight="bold",
                 fontsize=11.5)
    return _save(fig, path)
