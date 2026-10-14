"""Events agent: makes the forecast react to what is happening in the world.

A war, sanctions, tariffs, a regulation or a supply shock changes a market's
growth path (e.g. crude oil and a US-Iran conflict). Steps:

  1. search    the LLM proposes news queries for events that could move this
               market's demand, supply or prices (geopolitics, trade,
               regulation, technology, macro); run against DATED news results
  2. extract   events with source, date and a verbatim quote; code keeps an
               event only if the quote is in the article text and the date is
               within `max_age_days`
  3. quantify  assumptor-style: direction, annual growth impact (percentage
               points) while the event lasts, duration in years, affected
               regions / countries, confidence, mechanism -- with reasoning
  4. apply     in code, bounded: effect on the 2025-2033 CAGR =
                   impact_pp x min(duration, horizon) / horizon x confidence
               |per event| <= MAX_EVENT_PP, |sum per geography| <= MAX_TOTAL_PP.
               Regional and country CAGRs move; the global CAGR is recomputed
               value-weighted. Baseline and adjusted CAGRs are both kept.

Every event, source, quote and number is returned for the report.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from openpyxl import load_workbook

from .structure_agent import _norm

MAX_EVENT_PP = 3.0
MAX_TOTAL_PP = 5.0
HORIZON = 8  # 2025 -> 2033


def news(query: str, max_results: int = 8, timelimit: str = "y") -> list[dict[str, str]]:
    try:
        from ddgs import DDGS
        rows = DDGS().news(query, max_results=max_results, timelimit=timelimit) or []
    except Exception:  # noqa: BLE001 -- no news is not an error
        return []
    return [{"date": str(r.get("date") or "")[:10], "title": str(r.get("title") or ""), "body": str(r.get("body") or ""),
             "url": str(r.get("url") or ""), "source": str(r.get("source") or "")} for r in rows]


def propose_queries(llm: Any, market: str, segments: dict[str, list[str]], regions: list[str]) -> list[str]:
    data = llm.complete_json(
        "You are a market analyst scanning the news for events that change a market's growth: wars and conflicts, "
        "sanctions, tariffs and trade disputes, regulation, supply disruptions, price shocks, technology "
        "breakthroughs, demand shifts, macro shocks. Return json.",
        f"Market: {market}\nSegments: {json.dumps(segments, ensure_ascii=False)[:1500]}\nRegions: {regions}\n"
        "Give 8 short news-search queries (3-7 words) most likely to surface recent events affecting this market's "
        'demand, supply or prices.\nReturn JSON: {"queries": ["..."]}',
    )
    qs = [str(q) for q in data.get("queries") or [] if str(q).strip()][:8]
    term = market.replace("Global", "").replace("Market", "").strip()
    return qs + [f"{term} market outlook", f"{term} demand war sanctions tariffs"]


def extract_events(llm: Any, market: str, articles: list[dict[str, str]], regions: list[str],
                   geographies: list[str], today: datetime, max_age_days: int) -> list[dict[str, Any]]:
    block = "\n".join(f"[{i}] {a['date']} | {a['source']} | {a['title']} -- {a['body']}" for i, a in enumerate(articles))
    data = llm.complete_json(
        "You turn news into forecast adjustments for one market. List each DISTINCT event that materially changes "
        "this market's growth over 2025-2033 (ignore routine price moves and generic commentary). For each: the "
        "verbatim sentence from the article, direction, the annual growth impact in percentage points WHILE the "
        "event lasts (e.g. -2.0 = growth 2 points lower per year), duration in years, which regions/countries it "
        "affects (from the lists, or 'Global'), confidence 0-1 that it plays out, and the mechanism. Be realistic: "
        "most events move growth by well under 2 points; reserve larger numbers for major wars, embargoes or "
        "structural shifts. Return json.",
        f"Market: {market}\nToday: {today:%Y-%m-%d}\nRegions: {regions}\nCountries: {geographies}\n\nNews:\n{block}\n\n"
        'Return JSON: {"events": [{"event": "<short name>", "article_index": <int>, "quote": "<verbatim>", '
        '"direction": "<up|down>", "impact_pp_per_year": <number>, "duration_years": <number>, '
        '"affects": ["Global" or region/country names], "confidence": <0-1>, "mechanism": "<one sentence>"}]}',
    )
    out, seen = [], set()
    for e in data.get("events") or []:
        idx = e.get("article_index")
        if not isinstance(idx, int) or not 0 <= idx < len(articles):
            continue
        art = articles[idx]
        quote = str(e.get("quote") or "")
        text = f"{art['title']} {art['body']}"
        if not quote or quote[:50].lower() not in text.lower():
            continue  # the quote must be in the article
        try:
            age = (today - datetime.fromisoformat(art["date"]).replace(tzinfo=timezone.utc)).days
        except ValueError:
            continue
        if age > max_age_days or age < -2:
            continue
        try:
            pp = float(e.get("impact_pp_per_year"))
            dur = max(0.25, float(e.get("duration_years") or 1))
            conf = min(1.0, max(0.0, float(e.get("confidence") or 0.5)))
        except (TypeError, ValueError):
            continue
        if str(e.get("direction") or "").lower() == "down":
            pp = -abs(pp)
        elif str(e.get("direction") or "").lower() == "up":
            pp = abs(pp)
        name = str(e.get("event") or "")[:100]
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        affects = [a for a in (e.get("affects") or ["Global"]) if a == "Global" or a in regions or a in geographies]
        eff = max(-MAX_EVENT_PP, min(MAX_EVENT_PP, pp * min(dur, HORIZON) / HORIZON * conf))
        out.append({"event": name, "date": art["date"], "source": art["source"], "url": art["url"], "quote": quote[:300],
                    "impact_pp_per_year": pp, "duration_years": dur, "confidence": conf,
                    "affects": affects or ["Global"], "mechanism": str(e.get("mechanism") or "")[:240],
                    "effective_cagr_pp": round(eff, 3), "age_days": age})
    return out


def geo_deltas(events: list[dict[str, Any]], regions: dict[str, list[str]]) -> dict[str, float]:
    """Total CAGR change (percentage points) per region and per country."""
    names = list(regions) + [c for cs in regions.values() for c in cs]
    region_of = {c: r for r, cs in regions.items() for c in cs}
    out = {n: 0.0 for n in names}
    for e in events:
        for n in names:
            hit = ("Global" in e["affects"] or n in e["affects"]
                   or (n in region_of and region_of[n] in e["affects"]))
            if hit:
                out[n] += e["effective_cagr_pp"]
    return {n: max(-MAX_TOTAL_PP, min(MAX_TOTAL_PP, v)) for n, v in out.items()}


def scan(llm: Any, market: str, segments: dict[str, list[str]], regions: dict[str, list[str]],
         geographies: list[str], max_age_days: int = 540, today: datetime | None = None,
         search: Callable[[str], list[dict[str, str]]] = news) -> dict[str, Any]:
    today = today or datetime.now(timezone.utc)
    queries = propose_queries(llm, market, segments, list(regions))
    articles, urls = [], set()
    for q in queries:
        for a in search(q):
            if a["url"] and a["url"] not in urls:
                urls.add(a["url"])
                articles.append(a)
    articles = articles[:60]
    events = extract_events(llm, market, articles, list(regions), geographies, today, max_age_days) if articles else []
    return {"queries": queries, "articles_read": len(articles), "events": events,
            "deltas_pp": geo_deltas(events, regions), "scanned_at": today.isoformat()}


def apply(data_file: Path, scan_result: dict[str, Any], regions: dict[str, list[str]]) -> dict[str, Any]:
    """Move CAGRs in the Data file by the event deltas, consistently up the tree:
      country  = its direct events (Global + its region + itself), bounded
      region   = value-weighted average of its countries' deltas
      global   = baseline + value-weighted average of the regions' deltas
    (never recomputed from scratch, so an inconsistent baseline can't flip the sign)."""
    direct = scan_result["deltas_pp"]
    wb = load_workbook(data_file)
    ws = wb["Data"]
    wsv = load_workbook(data_file, data_only=True)["Data"]  # cached values: region-block cells may be formulas
    labels = [_norm(ws.cell(r, 2).value) for r in range(1, ws.max_row + 1)]
    start = next((i for i, lab in enumerate(labels, start=1) if lab == "Region"), None)
    rows, values, base = {}, {}, {}
    if start:
        for r in range(start + 1, ws.max_row + 1):
            lab = _norm(ws.cell(r, 2).value)
            if lab:
                rows[lab] = r
                if isinstance(wsv.cell(r, 5).value, (int, float)):
                    values[lab] = float(wsv.cell(r, 5).value)
                if isinstance(wsv.cell(r, 4).value, (int, float)):
                    base[lab] = float(wsv.cell(r, 4).value)
    delta: dict[str, float] = {}
    for reg, cs in regions.items():
        for c in cs:
            delta[c] = direct.get(c, 0.0)
        w = {c: values.get(c, 0.0) for c in cs}
        tw = sum(w.values())
        delta[reg] = (sum(w[c] * delta[c] for c in cs) / tw) if tw else direct.get(reg, 0.0)
    changes = []
    for name, d in delta.items():
        r = rows.get(name)
        if r is None or name not in base or not d:
            continue
        new = max(-0.2, base[name] + d / 100)
        ws.cell(r, 4).value = new
        changes.append({"geo": name, "baseline_cagr": base[name], "adjusted_cagr": new, "delta_pp": round(d, 3)})
    rv = {reg: values.get(reg) or sum(values.get(c, 0.0) for c in cs) for reg, cs in regions.items()}
    tv = sum(rv.values())
    global_delta = (sum(rv[reg] * delta[reg] for reg in regions) / tv) if tv else 0.0
    wsm = wb["Market Value"]
    hdr = next(r for r in range(1, 10) if any(_norm(wsm.cell(r, c).value).lower() in ("regions", "region")
                                                for c in range(1, wsm.max_column + 1)))
    cagr_c = next((c for c in range(1, wsm.max_column + 1) if _norm(wsm.cell(hdr, c).value).lower() == "cagr"), None)
    wsmv = load_workbook(data_file, data_only=True)["Market Value"]
    gbase = gnew = None
    if cagr_c:
        for r in range(hdr + 1, wsm.max_row + 1):
            name = _norm(wsm.cell(r, 2).value)
            b = wsmv.cell(r, cagr_c).value
            if not isinstance(b, (int, float)):
                continue
            if name == "Global":
                gbase, gnew = b, max(-0.2, b + global_delta / 100)
                wsm.cell(r, cagr_c).value = gnew
            elif name in delta:
                wsm.cell(r, cagr_c).value = max(-0.2, b + delta[name] / 100)
    wb.save(data_file)
    return {"changes": changes, "global_cagr_baseline": gbase, "global_cagr_adjusted": gnew,
            "global_delta_pp": round(global_delta, 3)}


def markdown(scan_result: dict[str, Any], applied: dict[str, Any] | None = None) -> str:
    L = ["## Real-world events applied to the forecast", "",
         f"{scan_result['articles_read']} dated news articles read ({len(scan_result['queries'])} queries); "
         f"{len(scan_result['events'])} events kept (quote verified in the article, recent).", "",
         "| Event | Date | Source | Impact while it lasts | Duration | Confidence | Effect on 2025-33 CAGR | Affects |",
         "|---|---|---|---|---|---|---|---|"]
    for e in sorted(scan_result["events"], key=lambda e: -abs(e["effective_cagr_pp"])):
        L.append(f"| {e['event']} | {e['date']} | [{e['source']}]({e['url']}) | {e['impact_pp_per_year']:+.1f} pp/yr | "
                 f"{e['duration_years']:g} yr | {e['confidence']:.0%} | {e['effective_cagr_pp']:+.2f} pp | "
                 f"{', '.join(e['affects'])} |")
    L += [""] + [f"- **{e['event']}** — {e['mechanism']} “{e['quote'][:200]}”" for e in scan_result["events"]]
    if applied:
        if applied.get("global_cagr_adjusted") is not None and applied.get("global_cagr_baseline") is not None:
            L += ["", f"Global CAGR: baseline {applied['global_cagr_baseline']:.2%} → event-adjusted "
                      f"{applied['global_cagr_adjusted']:.2%}"]
        regs = [c for c in applied["changes"]]
        if regs:
            L += ["", "| Geography | Baseline CAGR | Event-adjusted | Δ pp |", "|---|---|---|---|"]
            L += [f"| {c['geo']} | {c['baseline_cagr']:.2%} | {c['adjusted_cagr']:.2%} | {c['delta_pp']:+.2f} |"
                  for c in regs]
    return "\n".join(L)
