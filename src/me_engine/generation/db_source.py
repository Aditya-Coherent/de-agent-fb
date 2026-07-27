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

from .semantic_search import ensure_embedded, nearest_report


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


_REPORT_COLUMNS = '"newsId", keyword, forcastyear, summary, segmentation, "newsLDesc"'


def _row_to_evidence(row, fallback_name: str) -> DBEvidence:
    news_id, keyword, fy, summary, segmentation, ldesc = row
    narrative = _strip_html(ldesc) or _strip_html(summary)
    return DBEvidence(
        news_id=news_id,
        keyword=keyword or fallback_name,
        forecast_year=fy or "",
        summary_text=_strip_html(summary),
        segmentation_tree=_parse_seg_tree(segmentation),
        raw_narrative=narrative,
        raw_segmentation=segmentation or "",
    )


def fetch_market_evidence(neon_url: str, market_name: str) -> DBEvidence | None:
    """Look up the best matching report by keyword/subject and extract evidence.

    Two-tier lookup: fast exact/substring (LIKE) match first. If that finds
    nothing — the requested market name doesn't share a literal substring with
    any report's keyword/subject — fall back to local-embedding semantic
    search (semantic_search.nearest_report), so a differently-worded but
    equivalent market name ("EV Charging Market" vs "Electric Vehicle
    Charging Infrastructure Market") can still find its report. The semantic
    fallback silently returns nothing if embeddings haven't been backfilled
    (or only partially backfilled) for the matching report — same as if no
    report existed, never an error.

    Note: a LIKE hit can still be the WRONG report (a generic word like
    "retail" can match an unrelated report's newsSubject category tag). This
    function doesn't know that — the caller must still run `verify_market_match`
    and, on rejection, retry with `fetch_market_evidence_semantic` (which skips
    the LIKE step entirely) for a genuine second opinion.
    """
    terms = _search_terms(market_name)
    with psycopg.connect(neon_url) as conn, conn.cursor() as cur:
        row = None
        for term in terms:
            cur.execute(
                f"""
                SELECT {_REPORT_COLUMNS}
                FROM cmi_reports
                WHERE (lower(keyword) LIKE %s OR lower("newsSubject") LIKE %s)
                  AND length(coalesce(segmentation,'')) > 400
                ORDER BY length(coalesce(segmentation,'')) DESC
                LIMIT 1
                """,  # type: ignore[arg-type]
                (f"%{term}%", f"%{term}%"),
            )
            row = cur.fetchone()
            if row:
                break

        if row:
            # Lazily embed this report if it doesn't have one yet — grows
            # semantic-search coverage organically through real usage instead
            # of requiring a big upfront batch backfill.
            news_id, keyword = row[0], row[1]
            ensure_embedded(cur, news_id, keyword or market_name)
        else:
            hit = nearest_report(cur, market_name)
            if hit:
                news_id, _keyword, _similarity = hit
                cur.execute(
                    f'SELECT {_REPORT_COLUMNS} FROM cmi_reports WHERE "newsId" = %s',  # type: ignore[arg-type]
                    (news_id,),
                )
                row = cur.fetchone()

    if not row:
        return None
    return _row_to_evidence(row, market_name)


def fetch_market_evidence_semantic(neon_url: str, market_name: str) -> DBEvidence | None:
    """Semantic-only lookup (skips the LIKE step entirely).

    Used as a second attempt when `fetch_market_evidence`'s LIKE hit was
    rejected by `verify_market_match` — a plain word match can land on a
    completely unrelated report, and simply giving up loses evidence that a
    meaning-based search would still find.
    """
    with psycopg.connect(neon_url) as conn, conn.cursor() as cur:
        hit = nearest_report(cur, market_name)
        if not hit:
            return None
        news_id, _keyword, _similarity = hit
        cur.execute(
            f'SELECT {_REPORT_COLUMNS} FROM cmi_reports WHERE "newsId" = %s',  # type: ignore[arg-type]
            (news_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return _row_to_evidence(row, market_name)


def verify_market_match(market_name: str, narrative: str, llm) -> bool:
    """Ask the LLM whether a fetched report's narrative genuinely describes the
    requested market (guards against the keyword LIKE-match picking a wrong,
    unrelated report and poisoning every downstream evidence brief).

    Defaults to True (keep the evidence) when there's nothing to check or the
    verification call itself fails — we only want to reject a clear mismatch,
    never silently drop marginal-but-real evidence over a flaky LLM call.
    """
    if not narrative.strip():
        return True
    system = (
        "You check whether a market-research report's narrative matches a "
        "requested market name. Judge only from the text given."
    )
    user = (
        f"Requested market: {market_name}\n\n"
        f"Report narrative (may be truncated):\n{narrative[:1200]}\n\n"
        'Return JSON: {"match": true|false, "reason": "<short>"}. '
        "match=false ONLY if the narrative is clearly about a different, "
        "unrelated market — not merely a different phrasing of the same market."
    )
    try:
        data = llm.complete_json(system, user)
        return bool(data.get("match", True))
    except Exception:  # noqa: BLE001
        return True


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
