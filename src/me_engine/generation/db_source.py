"""Neon DB source: fetch a market report and extract structure + summary evidence.

We use the DB purely as *evidence* (segmentation structure + narrative with market
size / CAGR). Numbers found here are treated as priors the agents may adopt or
override — never as ground truth copied from the human input sheet.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

import psycopg


@dataclass(slots=True)
class DBEvidence:
    news_id: int | None
    keyword: str
    forecast_year: str
    summary_text: str
    segmentation_tree: list[str]  # indented "depth|label" lines
    raw_narrative: str
    raw_segmentation: str = ""    # original segmentation HTML (for dynamic skeleton)


def _strip_html(raw: str | None) -> str:
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _parse_seg_tree(raw: str | None) -> list[str]:
    """Parse a nested <ul>/<li> segmentation into "depth|label" lines.

    Robust to labels wrapped in inner tags (e.g. <li><p>Label</p><ul>...): we take
    each <li>'s own text up to its first child <ul> (or </li>) and strip all tags.
    """
    if not raw:
        return []
    out: list[str] = []
    depth = 0
    for m in re.finditer(r"<ul>|</ul>|<li[^>]*>(.*?)(?=<ul>|</li>)", raw, flags=re.S):
        tok = m.group(0)
        if tok == "<ul>":
            depth += 1
            continue
        if tok == "</ul>":
            depth = max(0, depth - 1)
            continue
        label = _strip_html(m.group(1) or "")
        if label:
            out.append(f"{depth}|{label}")
    return out


def fetch_market_evidence(neon_url: str, market_name: str) -> DBEvidence | None:
    """Look up the best matching report by keyword/subject and extract evidence."""
    terms = _search_terms(market_name)
    with psycopg.connect(neon_url) as conn, conn.cursor() as cur:
        row = None
        for term in terms:
            cur.execute(
                """
                SELECT "newsId", keyword, forcastyear, summary, segmentation, "newsLDesc"
                FROM cmi_reports
                WHERE (lower(keyword) LIKE %s OR lower("newsSubject") LIKE %s)
                  AND length(coalesce(segmentation,'')) > 400
                ORDER BY length(coalesce(segmentation,'')) DESC
                LIMIT 1
                """,
                (f"%{term}%", f"%{term}%"),
            )
            row = cur.fetchone()
            if row:
                break
    if not row:
        return None
    news_id, keyword, fy, summary, segmentation, ldesc = row
    narrative = _strip_html(ldesc) or _strip_html(summary)
    return DBEvidence(
        news_id=news_id,
        keyword=keyword or market_name,
        forecast_year=fy or "",
        summary_text=_strip_html(summary),
        segmentation_tree=_parse_seg_tree(segmentation),
        raw_narrative=narrative,
        raw_segmentation=segmentation or "",
    )


def _search_terms(market_name: str) -> list[str]:
    base = market_name.lower()
    for junk in ["global ", "the ", " market", "solutions", "-"]:
        base = base.replace(junk, " ")
    words = [w for w in base.split() if len(w) > 2]
    terms = [market_name.lower().replace(" market", "").strip()]
    # progressively shorter phrases improve match odds
    if len(words) >= 2:
        terms.append(" ".join(words[:3]))
        terms.append(" ".join(words[:2]))
    terms.extend(words[:2])
    seen: list[str] = []
    for t in terms:
        t = t.strip()
        if t and t not in seen:
            seen.append(t)
    return seen
