"""Major players + market-coverage estimation.

1. ``find_major_players``: early in the run, search the web (existing search router) for the
   largest companies / market-share pages of the market, then ONE LLM call grounded in those
   results returns the biggest companies with an ESTIMATED annual revenue in this market (USD Mn),
   an ESTIMATED global market share (%) and the ESTIMATED total market size it used.
2. ``estimate_revenues``: batched LLM estimates for every other kept manufacturer (so coverage is
   the sum over everything we keep, not only the majors).
3. ``estimate_coverage``: sum of Est_Market_Share_Pct over rows kept as manufacturers (<=100%).
4. ``expand_major_players``: targeted searches + LLM for ADDITIONAL large players not yet found,
   used by the coverage loop (``run_coverage_loop``) until the target coverage or the time budget.

Every number here is an ESTIMATE (LLM reading of search snippets / model knowledge) and is
labelled as such in the outputs.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Awaitable, Callable

ESTIMATE_NOTE = (
    "All revenue / market-share / market-size figures are ESTIMATES produced by an LLM from web "
    "search snippets and model knowledge; they are approximate and not audited financial data."
)

_MAJORS_SYS = """You are a market-sizing analyst. Using the WEB SEARCH EVIDENCE provided (numbered
snippets and page excerpts) plus your own knowledge, identify the LARGEST companies in the given
market by revenue earned IN THIS MARKET (not total group revenue, unless the company is a pure
player) and estimate:
- est_revenue_usd_mn: approximate annual revenue from THIS market, in USD millions (number)
- est_market_share_pct: approximate share of the GLOBAL market (or the stated geography), in %
Also estimate the TOTAL market size (USD millions, recent year) you are using as the denominator.

Return JSON only:
{"market_size_usd_mn": <number or null>, "market_size_year": <int or null>,
 "market_size_source": "<evidence refs like [2], or a URL, or 'model knowledge'>",
 "companies": [{"name": "<company>", "domain": "<official bare domain or ''>",
   "hq_country": "<country>", "est_revenue_usd_mn": <number or null>,
   "est_market_share_pct": <number or null>,
   "source": "<evidence refs like [3][7], or a URL, or 'model knowledge'>"}]}

RULES:
- Real market participants only (producers / manufacturers / brand owners / processors) - never
  market-research firms, news sites, retailers that only resell, or associations.
- 15-35 companies, largest first. Include regional leaders that hold meaningful share.
- List each corporate GROUP once with its group-level revenue in this market; do not list its
  brands/subsidiaries as separate companies (that would double count).
- Numbers are approximate estimates; use null when you genuinely cannot estimate.
- Shares must be consistent with the total (share ~= revenue / total * 100) and must not sum
  above 100."""

_MORE_SYS = """You are a market-sizing analyst. The companies in ALREADY_FOUND are known. Using the
WEB SEARCH EVIDENCE plus your knowledge, list ADDITIONAL large companies in this market that are
NOT in ALREADY_FOUND (other names of the same group count as already found), largest first, with
ESTIMATED revenue in THIS market (USD Mn) and ESTIMATED market share (%) against the given total.
Return JSON only: {"companies": [{"name": "...", "domain": "<official bare domain or ''>",
"hq_country": "...", "est_revenue_usd_mn": <number or null>, "est_market_share_pct": <number or null>,
"source": "<evidence refs like [3], or a URL, or 'model knowledge'>"}]}
Real market participants only; up to 20 companies; return an empty list if you know none."""

_REVENUE_SYS = """You estimate company size in a specific market. For EACH company listed, estimate
its approximate annual revenue earned in THIS market (USD millions) and its approximate share of
the total market (given in USD millions). Use your knowledge of the company; for small/private
firms give a cautious order-of-magnitude estimate ONLY if you have a reasonable basis, otherwise
null. Never inflate. Return JSON only:
{"items": [{"i": <index>, "est_revenue_usd_mn": <number or null>, "est_market_share_pct": <number or null>}]}"""


# ----------------------------------------------------------------------------------------------
# Pure helpers (unit-tested)
# ----------------------------------------------------------------------------------------------


_LEGAL_TOKENS = frozenset(
    "inc ltd llc corp corporation gmbh ag sa spa plc co company group holding holdings limited "
    "s a p l srl sl bv nv the".split()
)


def name_key(s: Any) -> str:
    """Normalised company key: lowercase alnum without legal-form tokens ('Deoleo S.A.' == 'Deoleo')."""
    toks = re.split(r"[^a-z0-9]+", str(s or "").lower())
    kept = [t for t in toks if t and t not in _LEGAL_TOKENS]
    return "".join(kept or [t for t in toks if t])


def clean_domain(d: Any) -> str:
    d = str(d or "").strip().lower()
    d = d.removeprefix("http://").removeprefix("https://").removeprefix("www.")
    return d.split("/")[0].split("?")[0].strip(".")


def _num(v: Any) -> float | None:
    if v is None or v == "" or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
    else:
        m = re.search(r"-?\d[\d,]*\.?\d*", str(v))
        if not m:
            return None
        try:
            f = float(m.group(0).replace(",", ""))
        except ValueError:
            return None
    if f != f or f < 0:  # NaN / negative
        return None
    return f


def normalize_majors(raw: Any, *, total_override: float | None = None) -> dict[str, Any]:
    """Coerce the LLM payload: numbers -> float, fill share<->revenue from the total, clamp shares
    to [0, 100] and scale them down if they sum above 100, dedupe by name, sort by revenue desc
    (unknown revenue last)."""
    data = raw if isinstance(raw, dict) else {}
    total = total_override if total_override else _num(data.get("market_size_usd_mn"))
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for c in data.get("companies") or []:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or c.get("canonical_name") or "").strip()
        k = name_key(name)
        if not name or not k or k in seen:
            continue
        seen.add(k)
        rev = _num(c.get("est_revenue_usd_mn"))
        share = _num(c.get("est_market_share_pct"))
        if total and rev is not None and share is None:
            share = rev / total * 100.0
        if total and share is not None and rev is None:
            rev = share * total / 100.0
        if share is not None:
            share = max(0.0, min(100.0, share))
        rows.append(
            {
                "name": name,
                "domain": clean_domain(c.get("domain") or c.get("primary_domain")),
                "hq_country": str(c.get("hq_country") or "").strip(),
                "est_revenue_usd_mn": round(rev, 1) if rev is not None else None,
                "est_market_share_pct": round(share, 2) if share is not None else None,
                "source": str(c.get("source") or "model knowledge").strip()[:300],
            }
        )
    # "Bertolli (Deoleo)" when "Deoleo" is also listed: a brand/subsidiary of a listed group.
    # Its revenue is already inside the parent's figure -> flag it so coverage never double counts.
    keys = {name_key(r["name"]): r["name"] for r in rows}
    for r in rows:
        m = re.search(r"\(([^)]+)\)", r["name"])
        if not m:
            continue
        pk = name_key(m.group(1))
        for k, nm in keys.items():
            if nm != r["name"] and pk and (pk == k or (len(pk) >= 5 and (pk in k or k in pk))):
                r["part_of"] = nm
                break
    tot_share = sum(r["est_market_share_pct"] or 0.0 for r in rows if not r.get("part_of"))
    if tot_share > 100.0:
        f = 100.0 / tot_share
        for r in rows:
            if r["est_market_share_pct"] is not None:
                r["est_market_share_pct"] = round(r["est_market_share_pct"] * f, 2)
    rows.sort(key=lambda r: (r["est_revenue_usd_mn"] is None, -(r["est_revenue_usd_mn"] or 0.0)))
    return {
        "market_size_usd_mn": round(total, 1) if total else None,
        "market_size_year": data.get("market_size_year"),
        "market_size_source": str(data.get("market_size_source") or "").strip()[:300],
        "companies": rows,
    }


def match_major(row: dict[str, Any], majors: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Find the major-player record for an exported row (domain first, then fuzzy name)."""
    dom = clean_domain(row.get("domain") or row.get("website"))
    nk = name_key(row.get("company") or row.get("brand") or row.get("name"))
    for m in majors:
        md = clean_domain(m.get("domain"))
        if dom and md and (dom == md or dom.endswith("." + md) or md.endswith("." + dom)):
            return m
    if not nk:
        return None
    for m in majors:
        mk = name_key(m.get("name"))
        if not mk:
            continue
        if nk == mk or (len(nk) >= 5 and nk in mk) or (len(mk) >= 5 and mk in nk):
            return m
    return None


_MANUF_SECTION = re.compile(
    r"manufactur|producer|production|processor|processing|miller|refiner|bottler|packer|oem", re.I
)


def is_manufacturer_section(section_name: str) -> bool:
    s = str(section_name or "")
    if re.search(r"component|distribut|research|consult|industry bod", s, re.I):
        return False
    return bool(_MANUF_SECTION.search(s))


def is_manufacturer_row(row: dict[str, Any], section_name: str) -> bool:
    if str(section_name or "").strip().lower() == "multi-segment players":
        return any(
            is_manufacturer_section(str(s.get("section") or "")) or _MANUF_SECTION.search(str(s.get("role") or ""))
            for s in (row.get("multi_segments") or [])
            if isinstance(s, dict)
        )
    return is_manufacturer_section(section_name)


def estimate_coverage(grouped: list[tuple[str, list[dict[str, Any]]]]) -> float:
    """Estimated coverage (%) = sum of Est_Market_Share_Pct of relevant rows kept in manufacturer
    sections, bounded at 100."""
    total = 0.0
    seen: set[int] = set()
    for section, rows in grouped:
        for r in rows:
            if id(r) in seen or not r.get("is_relevant", True):
                continue
            seen.add(id(r))
            if not is_manufacturer_row(r, section) or r.get("part_of"):
                continue  # brands of an already-counted group are not double counted
            sh = _num(r.get("est_market_share_pct"))
            if sh:
                total += sh
    return round(min(100.0, total), 2)


def apply_estimates(
    rows: list[dict[str, Any]], majors: list[dict[str, Any]], total_mn: float | None
) -> int:
    """Copy major-player estimates onto matching rows (and mark them is_major). Returns count."""
    n = 0
    for r in rows:
        m = match_major(r, majors)
        if not m:
            continue
        r["is_major"] = True
        if m.get("est_revenue_usd_mn") is not None:
            r["est_revenue_usd_mn"] = m["est_revenue_usd_mn"]
        if m.get("est_market_share_pct") is not None:
            r["est_market_share_pct"] = m["est_market_share_pct"]
        elif total_mn and r.get("est_revenue_usd_mn") is not None:
            r["est_market_share_pct"] = round(float(r["est_revenue_usd_mn"]) / total_mn * 100.0, 2)
        r["estimate_source"] = m.get("source") or "model knowledge"
        if m.get("part_of"):
            r["part_of"] = m["part_of"]
        n += 1
    return n


def assign_ranks(rows: list[dict[str, Any]]) -> None:
    """Rank = position by estimated revenue (desc) among rows that have an estimate."""
    known = [r for r in rows if _num(r.get("est_revenue_usd_mn")) is not None]
    known.sort(key=lambda r: -float(r["est_revenue_usd_mn"]))
    for r in rows:
        r.pop("revenue_rank", None)
    for i, r in enumerate(known, 1):
        r["revenue_rank"] = i


def revenue_sort_key(r: dict[str, Any]) -> tuple:
    rev = _num(r.get("est_revenue_usd_mn"))
    return (rev is None, -(rev or 0.0))


async def run_coverage_loop(
    compute_coverage: Callable[[], float],
    expand_once: Callable[[int], Awaitable[int]],
    *,
    target_pct: float,
    budget: Any,
    max_rounds: int = 3,
    min_remaining_sec: float = 90.0,
) -> dict[str, Any]:
    """Expand with targeted searches until coverage >= target, the time budget is in wrap-up /
    nearly exhausted, a round adds nothing new, or max_rounds is reached."""
    cov = float(compute_coverage())
    history = [cov]
    rounds = 0
    reason = "target_reached" if cov >= target_pct else ""
    while cov < target_pct:
        if rounds >= max_rounds:
            reason = "max_rounds"
            break
        if budget is not None and (budget.wrap_up() or budget.remaining() < min_remaining_sec):
            reason = "time_budget"
            break
        added = int(await expand_once(rounds) or 0)
        rounds += 1
        cov = float(compute_coverage())
        history.append(cov)
        if cov >= target_pct:
            reason = "target_reached"
            break
        if added <= 0:
            reason = "no_new_players"
            break
    return {"coverage_pct": cov, "rounds": rounds, "stop_reason": reason or "target_reached", "history": history}


# ----------------------------------------------------------------------------------------------
# Search + LLM steps
# ----------------------------------------------------------------------------------------------


def _is_global(geo: str) -> bool:
    return (geo or "global").strip().lower() in ("", "global", "worldwide", "world", "international")


async def _gather_evidence(
    queries: list[str], market: str, geo: str, settings: Any, *, max_rows: int = 45, fetch_pages: int = 3
) -> tuple[str, list[dict[str, str]]]:
    """Run the queries concurrently via the search router; return (evidence_text, sources)."""
    from vendor_intel.clients.search_router import FreeSearchRouter

    router = FreeSearchRouter(settings)
    g = "" if _is_global(geo) else geo

    async def _one(q: str):
        try:
            return await router.search(q, market=market, geo=g, search_topic=market, discovery_mode=False)
        except Exception:
            return []

    results = await asyncio.gather(*[_one(q) for q in queries])
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for res in results:
        for r in res or []:
            link = str(getattr(r, "link", "") or "")
            key = link.split("#")[0].rstrip("/").lower()
            if not link or key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "title": str(getattr(r, "title", "") or "")[:160],
                    "snippet": str(getattr(r, "snippet", "") or "")[:400],
                    "url": link,
                }
            )
    rows = rows[:max_rows]
    # Read a few of the most promising pages (market-share / key-player summaries) for richer facts.
    pat = re.compile(r"market share|key players|top \d+|largest|leading|companies|manufacturers", re.I)
    picks = [r for r in rows if pat.search(r["title"] + " " + r["snippet"])][:fetch_pages]
    page_texts: list[str] = []
    if picks:
        from vendor_intel.scraping.ddgs_extract import fetch_page_via_ddgs_extract

        async def _page(u: str) -> str:
            try:
                p = await asyncio.wait_for(asyncio.to_thread(fetch_page_via_ddgs_extract, u), timeout=30)
                return (p.text or "")[:3500] if getattr(p, "alive", False) else ""
            except Exception:
                return ""

        page_texts = await asyncio.gather(*[_page(r["url"]) for r in picks])
    lines = [f"[{i}] {r['title']} | {r['snippet']} | {r['url']}" for i, r in enumerate(rows, 1)]
    for r, t in zip(picks, page_texts):
        if t and len(t.strip()) > 200:
            lines.append(f"\nPAGE EXCERPT ({r['url']}):\n{t}")
    return "\n".join(lines)[:22000], rows


def major_player_queries(market: str, geo: str) -> list[str]:
    m = market.strip()
    m_short = re.sub(r"\bmarket\b", "", m, flags=re.I).strip() or m
    loc = "" if _is_global(geo) else f" in {geo}"
    return [
        f"largest {m_short} companies by revenue{loc}",
        f"{m} share key players{loc}",
        f"top {m_short} manufacturers{loc}",
        f"{m} size key companies{loc}",
        f"leading {m_short} producers market share{loc}",
    ]


async def find_major_players(
    market: str, geo: str, settings: Any, client: Any
) -> dict[str, Any]:
    """Web-grounded major players with estimated revenue/share + the market-size estimate."""
    if not getattr(client, "available", False):
        return {"companies": [], "market_size_usd_mn": None, "evidence_sources": []}
    evidence, sources = await _gather_evidence(major_player_queries(market, geo), market, geo, settings)
    user = (
        f"MARKET: {market}\nGEOGRAPHY: {geo or 'global'}\n\n"
        f"WEB SEARCH EVIDENCE:\n{evidence or '(no search results - use model knowledge)'}"
    )
    raw = await asyncio.to_thread(
        lambda: client.complete_json(_MAJORS_SYS, user, max_tokens=4096)
    )
    out = normalize_majors(raw)
    out["evidence_sources"] = [s["url"] for s in sources[:25]]
    _resolve_evidence_refs(out["companies"], sources)
    if out.get("market_size_source"):
        out["market_size_source"] = _ref_to_urls(out["market_size_source"], sources)
    return out


def _ref_to_urls(src: str, sources: list[dict[str, str]]) -> str:
    """Turn '[3], [7]' style evidence references into URLs (keeps free text otherwise)."""
    src = str(src or "")
    nums = [int(n) for n in re.findall(r"\[(\d{1,3})\]", src)]
    if not nums and re.fullmatch(r"[\d,;\s]+(and\s+\d+)?", src.strip() or "x"):
        nums = [int(n) for n in re.findall(r"\d+", src)]
    urls = [sources[n - 1]["url"] for n in nums if 1 <= n <= len(sources)]
    if urls and not re.search(r"https?://", str(src)):
        return "; ".join(dict.fromkeys(urls))
    return str(src or "")


def _resolve_evidence_refs(companies: list[dict[str, Any]], sources: list[dict[str, str]]) -> None:
    for c in companies:
        c["source"] = _ref_to_urls(c.get("source") or "", sources) or "model knowledge"


async def expand_major_players(
    market: str,
    geo: str,
    settings: Any,
    client: Any,
    *,
    known_names: list[str],
    total_mn: float | None,
    round_idx: int,
    hq_countries: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Targeted searches for additional large players not yet found (coverage loop step)."""
    m = market.strip()
    m_short = re.sub(r"\bmarket\b", "", m, flags=re.I).strip() or m
    qs = [
        f"top {m_short} companies",
        f"{m_short} manufacturers market share",
        f"biggest {m_short} brands worldwide" if _is_global(geo) else f"biggest {m_short} brands {geo}",
    ]
    if round_idx >= 1:
        qs = [f"{m_short} industry leaders annual revenue", f"{m_short} exporters largest"]
    for ctry in (hq_countries or [])[round_idx * 3 : round_idx * 3 + 3]:
        qs.append(f"leading {m_short} producers in {ctry}")
    evidence, sources = await _gather_evidence(qs, market, geo, settings, max_rows=40, fetch_pages=2)
    user = (
        f"MARKET: {market}\nGEOGRAPHY: {geo or 'global'}\n"
        f"TOTAL MARKET SIZE (estimate, USD Mn): {total_mn if total_mn else 'unknown'}\n"
        f"ALREADY_FOUND: {json.dumps(known_names[:150])}\n\nWEB SEARCH EVIDENCE:\n{evidence}"
    )
    raw = await asyncio.to_thread(lambda: client.complete_json(_MORE_SYS, user, max_tokens=3000))
    out = normalize_majors(raw, total_override=total_mn)
    known = {name_key(n) for n in known_names}
    fresh = []
    for c in out["companies"]:
        k = name_key(c["name"])
        if any(k == kk or (len(k) >= 5 and k in kk) or (len(kk) >= 5 and kk in k) for kk in known):
            continue
        fresh.append(c)
    _resolve_evidence_refs(fresh, sources)
    return fresh


async def estimate_revenues(
    rows: list[dict[str, Any]],
    market: str,
    total_mn: float | None,
    client: Any,
    *,
    batch_size: int = 25,
    concurrency: int = 6,
) -> int:
    """Batched (concurrent) LLM revenue/share ESTIMATES for rows that have none yet."""
    todo = [r for r in rows if _num(r.get("est_revenue_usd_mn")) is None]
    if not todo or not getattr(client, "available", False):
        return 0
    batches = [todo[i : i + batch_size] for i in range(0, len(todo), batch_size)]
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _one(batch: list[dict[str, Any]]) -> int:
        payload = [
            {
                "i": i,
                "name": r.get("company") or r.get("brand") or "",
                "domain": clean_domain(r.get("domain") or r.get("website")),
                "what_they_do": str(r.get("market_role_detail") or r.get("role_description") or "")[:160],
            }
            for i, r in enumerate(batch)
        ]
        user = (
            f"MARKET: {market}\nTOTAL MARKET SIZE (estimate, USD Mn): {total_mn or 'unknown'}\n\n"
            f"COMPANIES:\n" + "\n".join(json.dumps(p, ensure_ascii=False) for p in payload)
        )
        async with sem:
            try:
                out = await asyncio.to_thread(
                    lambda: client.complete_json(_REVENUE_SYS, user, max_tokens=2500)
                )
            except Exception:
                return 0
        arr = out.get("items") if isinstance(out, dict) else out
        n = 0
        for x in arr or []:
            if not isinstance(x, dict):
                continue
            try:
                i = int(x.get("i"))
            except (TypeError, ValueError):
                continue
            if not 0 <= i < len(batch):
                continue
            rev = _num(x.get("est_revenue_usd_mn"))
            share = _num(x.get("est_market_share_pct"))
            if total_mn and rev is not None:
                share = rev / total_mn * 100.0  # keep share consistent with the shared denominator
            if total_mn and share is not None and rev is None:
                rev = share * total_mn / 100.0
            if rev is None and share is None:
                continue
            r = batch[i]
            if rev is not None:
                r["est_revenue_usd_mn"] = round(rev, 1)
            if share is not None:
                r["est_market_share_pct"] = round(max(0.0, min(100.0, share)), 2)
            r.setdefault("estimate_source", "LLM estimate (model knowledge)")
            n += 1
        return n

    counts = await asyncio.gather(*[_one(b) for b in batches])
    return int(sum(counts))
