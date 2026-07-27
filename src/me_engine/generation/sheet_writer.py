"""Write the generated driver set to an Input Sheet workbook.

Format mirrors the human Input Sheet: a `Data` sheet (per-country share-of-parent),
an `ASP` sheet (per-country prices), a `Market Value` sheet (regional value/CAGR),
and an `Artifacts` sheet (brain-agent justifications).

Agents decide ONE representative share vector per sibling group. We expand it to
per-country columns with a small *deterministic* dispersion (hash-seeded, no RNG)
and re-normalise each group within each country so shares still sum to 1.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Font, PatternFill

from .skeleton import Skeleton

_CONF_FILL = {
    "High": PatternFill("solid", fgColor="C6EFCE"),
    "Medium": PatternFill("solid", fgColor="FFEB9C"),
    "Low": PatternFill("solid", fgColor="FFC7CE"),
    "Unscored": PatternFill("solid", fgColor="F2F2F2"),
}


def _confidence(score: float | None, has_evidence: bool) -> tuple[str, str]:
    """Classify a reviewer score (+ whether real evidence backed it) into a
    (label, note) pair. Reflects how the number was actually arrived at —
    high LLM plausibility alone isn't "High" unless real evidence backed it,
    since a confident-sounding guess is still a guess."""
    if score is None:
        return "Unscored", "No reviewer score recorded for this run."
    if score >= 0.7 and has_evidence:
        return "High", f"Reviewer score {score:.2f}; backed by web evidence."
    if score >= 0.5:
        note = "backed by web evidence" if has_evidence else "no direct web evidence — plausibility-based only"
        return "Medium", f"Reviewer score {score:.2f}; {note}."
    detail = "evidence was available but the split still scored low" if has_evidence \
        else "no direct evidence and low reviewer plausibility"
    return "Low", f"Reviewer score {score:.2f}; needs review — {detail}."


def _write_confidence(ws, row: int, col: int, score: float | None, has_evidence: bool) -> None:
    label, note = _confidence(score, has_evidence)
    cell = ws.cell(row, col, label)
    cell.fill = _CONF_FILL[label]
    cell.comment = Comment(note, "DeAgent")


def _country_factor(key: str, spread: float = 0.12) -> float:
    h = int(hashlib.sha256(key.encode("utf-8")).hexdigest(), 16)
    return 1.0 + ((h % 1000) / 1000.0 - 0.5) * spread


def _dispersed_group(base_vec: dict[str, float], geo: str) -> dict[str, float]:
    raw = {seg: max(1e-6, share * _country_factor(f"{seg}|{geo}")) for seg, share in base_vec.items()}
    total = sum(raw.values())
    return {seg: round(v / total, 4) for seg, v in raw.items()}


def write_input_sheet(state: Mapping[str, Any], out_path: Path, flat: bool = False) -> Path:
    """Write the generated drivers to an Input Sheet workbook.

    flat=False (default): hierarchical share-of-parent rows (mirrors the human
        sheet; used for the deliverable and baseline comparison).
    flat=True: each dimension emits ONLY its leaf segments with compounded
        share-of-dimension (summing to 1.0), no parent rollup rows. This makes the
        sheet unambiguous for the ME pipeline regardless of how nested the market's
        segmentation is — handling segmentation variance across markets.
    """
    sk: Skeleton = state["skeleton"]
    shares = state["shares"]
    asp = state.get("asp", {})
    regional = state.get("regional", {})
    dim_score = state.get("dim_score") or {}
    dim_snippets = state.get("dim_snippets") or {}
    geos = sk.geographies
    # reverse map: geography -> region (for per-region share lookup)
    geo_region = {geo: region for region, members in sk.regions.items() for geo in members}

    def base_vector(dim: str, region: str | None, parent: str) -> dict[str, float]:
        dim_map = shares.get(dim, {})
        if region and region in dim_map and parent in dim_map[region]:
            return dim_map[region][parent]
        return dim_map.get("__global__", {}).get(parent, {})

    def compounded_leaves(dim: str, region: str | None) -> dict[str, float]:
        """Leaf share-of-dimension = product of share-of-parent down the tree."""
        result: dict[str, float] = {}
        tree = sk.nesting[dim]

        def walk(parent_key: str, acc: float) -> None:
            for seg, sh in base_vector(dim, region, parent_key).items():
                cs = acc * sh
                if seg in tree and seg != "__root__":  # seg is itself a group parent
                    walk(seg, cs)
                else:
                    result[seg] = result.get(seg, 0.0) + cs

        walk("__root__", 1.0)
        tot = sum(result.values()) or 1.0
        return {k: v / tot for k, v in result.items()}

    def country_leaf_vector(dim: str, geo: str) -> dict[str, float]:
        base = compounded_leaves(dim, geo_region.get(geo))
        raw = {seg: max(1e-6, sh * _country_factor(f"{seg}|{geo}")) for seg, sh in base.items()}
        tot = sum(raw.values())
        return {seg: round(v / tot, 4) for seg, v in raw.items()}

    wb = Workbook()
    bold = Font(bold=True)

    # ---------------- Data sheet ----------------
    ws = wb.active
    ws.title = "Data"
    ws.cell(1, 2, "Market Name").font = bold
    ws.cell(1, 3, sk.market_name)
    ws.cell(3, 1, "Segment Name in B Column Only")
    ws.cell(3, 2, "Segment").font = bold
    for ci, geo in enumerate(geos, start=3):
        ws.cell(3, ci, geo).font = bold
    conf_col = 3 + len(geos) + 1
    ws.cell(3, conf_col, "Confidence").font = bold

    r = 4
    for dim in sk.nesting:
        ws.cell(r, 2, dim).font = bold
        _write_confidence(ws, r, conf_col, dim_score.get(dim), bool(dim_snippets.get(dim)))
        r += 1
        if flat:
            # only leaf segments, compounded share-of-dimension (sum to 1 per country)
            for seg in sk.leaves(dim):
                ws.cell(r, 2, seg)
                for ci, geo in enumerate(geos, start=3):
                    ws.cell(r, ci, country_leaf_vector(dim, geo).get(seg, 0.0))
                r += 1
        else:
            # hierarchical share-of-parent rows (mirrors the human sheet)
            emitted: set[str] = set()
            for parent, children in sk.sibling_groups(dim):
                for seg in children:
                    if seg in emitted:
                        continue
                    emitted.add(seg)
                    ws.cell(r, 2, seg)
                    for ci, geo in enumerate(geos, start=3):
                        base_vec = base_vector(dim, geo_region.get(geo), parent)
                        disp = _dispersed_group(base_vec, geo)
                        ws.cell(r, ci, disp.get(seg, base_vec.get(seg, 0.0)))
                    r += 1
        r += 1  # blank spacer between dimensions

    # ---------------- Geography sizing block (required by ME pipeline) ----------
    # Layout mirrors the human sheet: B=Region/Country, C=Market Share,
    # D=Expected CAGR, E=Value. Global -> per region -> member countries.
    geo = state.get("geo", {})
    anchor = state.get("global_anchor_2026", 0.0)
    r = max(r + 2, 118)
    ws.cell(r, 1, "Always should start from 100th Row")
    ws.cell(r, 2, "Region").font = bold
    ws.cell(r, 3, "Market Share").font = bold
    ws.cell(r, 4, "Expected CAGR").font = bold
    ws.cell(r, 5, "Value").font = bold
    r += 1
    ws.cell(r, 2, "Global").font = bold
    ws.cell(r, 3, 1)
    ws.cell(r, 5, round(anchor, 2))
    r += 1
    for region, members in sk.regions.items():
        info = regional.get(region, {})
        rshare = info.get("value_share", 0.0)
        rvalue = anchor * rshare
        ws.cell(r, 2, region).font = bold
        ws.cell(r, 3, round(rshare, 4))
        ws.cell(r, 4, round(info.get("cagr", 0.0), 4))
        ws.cell(r, 5, round(rvalue, 2))
        r += 1
        for country in members:
            cinfo = geo.get(region, {}).get(country, {})
            cshare = cinfo.get("share_of_region", 1.0 / max(1, len(members)))
            ws.cell(r, 2, country)
            ws.cell(r, 3, round(cshare, 4))
            ws.cell(r, 4, round(cinfo.get("cagr", info.get("cagr", 0.1)), 4))
            ws.cell(r, 5, round(rvalue * cshare, 2))
            r += 1

    # ---------------- ASP sheet ----------------
    aws = wb.create_sheet("ASP")
    aws.cell(1, 2, "Market Name").font = bold
    aws.cell(1, 3, sk.market_name)
    aws.cell(2, 4, "Price Table").font = bold
    aws.cell(3, 3, f"{sk.priced_dimension}").font = bold
    for ci, geo in enumerate(geos, start=4):
        aws.cell(3, ci, geo).font = bold
    ar = 4
    aws.cell(ar, 3, sk.priced_dimension).font = bold
    asp_conf_col = 4 + len(geos) + 1
    aws.cell(3, asp_conf_col, "Confidence").font = bold
    _write_confidence(aws, ar, asp_conf_col, state.get("asp_score"), bool(state.get("web_snippets")))
    ar += 1
    priced_products = sk.leaves(sk.priced_dimension) if flat else sk.ordered_segments(sk.priced_dimension)
    for leaf in priced_products:
        aws.cell(ar, 3, leaf)
        base = asp.get(leaf, 100.0)
        for ci, geo in enumerate(geos, start=4):
            aws.cell(ar, ci, round(base * _country_factor(f"asp|{leaf}|{geo}", 0.08), 2))
        ar += 1

    # ---------------- Market Value sheet ----------------
    mv = wb.create_sheet("Market Value")
    mv.cell(2, 3, "Value").font = bold
    mv.cell(3, 2, "Regions").font = bold
    mv.cell(3, 3, "USD Mn").font = bold
    mv.cell(3, 4, "Share").font = bold
    mv.cell(3, 5, "CAGR").font = bold
    mv.cell(3, 6, "Confidence").font = bold
    anchor = state.get("global_anchor_2026", 0.0)
    mr = 4
    for region, info in regional.items():
        mv.cell(mr, 2, region)
        mv.cell(mr, 3, round(anchor * info["value_share"], 2))
        mv.cell(mr, 4, round(info["value_share"], 4))
        mv.cell(mr, 5, round(info["cagr"], 4))
        mr += 1
    mv.cell(mr, 2, "Global").font = bold
    mv.cell(mr, 3, round(anchor, 2))
    mv.cell(mr, 5, round(state.get("global_cagr", 0.0), 4))
    _write_confidence(mv, mr, 6, state.get("market_score"), bool(state.get("web_snippets")))

    # ---------------- Artifacts sheet ----------------
    art = wb.create_sheet("Artifacts")
    for ci, head in enumerate(["Topic", "Question", "Answer", "Drivers", "Current Events"], start=1):
        art.cell(1, ci, head).font = bold
    ir = 2
    for a in state.get("artifacts", []) or []:
        art.cell(ir, 1, str(a.get("topic", "")))
        art.cell(ir, 2, str(a.get("question", "")))
        art.cell(ir, 3, str(a.get("answer", "")))
        art.cell(ir, 4, "; ".join(a.get("drivers", []) or []))
        art.cell(ir, 5, "; ".join(a.get("current_events", []) or []))
        ir += 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return out_path
