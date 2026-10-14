"""Scope reconciliation for the market-size agent.

A published market size is only usable as the anchor if it measures the SAME
market the taxonomy describes. Seen: "Global High Energy Laser Market" -- the
web figure ($3.93B, a defense-centred directed-energy scope) was taken as the
whole market while the taxonomy covers industrial cutting/welding, medical,
sensing, telecom ... lasers as well, so every value came out ~6x low and
Defense was given ~39% of every country.

Evidence-driven, never calibrated to any reference file:

  1. partition   pick the end-use dimension (Application / End User / End Use)
                 whose top-level segments partition the market
  2. judge       the LLM reads the cited figure's sentence and says which of
                 those segments it covers (with a reason)
  3. fill gaps   for every uncovered segment, search the web for that
                 segment's own market size; a figure is accepted only if its
                 number literally appears in the snippet it came from
  4. reconcile   anchor = cited (covered part) + sum of accepted segment figures;
                 a segment with no cited figure gets an LLM estimate, flagged
                 as ungrounded. Components and the reasoning are kept.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

_PARTITION_HINTS = ("application", "end use", "end-use", "end user", "end-user", "vertical", "industry")
_MN = {"trillion": 1e6, "billion": 1e3, "bn": 1e3, "million": 1.0, "mn": 1.0}
_FIG = re.compile(r"(?:USD|US\$|\$)\s*([\d,]+(?:\.\d+)?)\s*(trillion|billion|million|bn|mn)\b", re.IGNORECASE)


def partition_dimension(nesting: dict[str, Any]) -> str | None:
    for hint in _PARTITION_HINTS:
        for dim in nesting:
            if hint in dim.lower():
                return dim
    return None


def product_term(market: str) -> str:
    t = re.sub(r"\b(global|market|industry)\b", "", market, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", t).strip()


def figures_in(text: str) -> list[float]:
    return [float(m.group(1).replace(",", "")) * _MN[m.group(2).lower()] for m in _FIG.finditer(text or "")]


def judge_coverage(llm: Any, market: str, cited: str, segments: list[str]) -> dict[str, Any]:
    data = llm.complete_json(
        "You check whether a published market-size figure measures the same market as a taxonomy. Read the "
        "figure's own wording (its market name, what it says it includes) and your knowledge of how such "
        "reports define that market. Return json.",
        f"Market being sized: {market}\nIts taxonomy's end-use segments (together they make up the whole market): "
        f"{json.dumps(segments, ensure_ascii=False)}\n\nCited figure: {cited}\n\n"
        "Which of these segments does the cited figure's market actually include? A segment counts as covered "
        "only if the figure's market definition includes it in full.\n"
        'Return JSON: {"covered": ["<segment names copied exactly>"], "scope_of_cited_figure": "<short>", '
        '"reason": "<one sentence>"}',
    )
    covered = [s for s in data.get("covered") or [] if s in segments]
    return {"covered": covered, "uncovered": [s for s in segments if s not in covered],
            "scope": str(data.get("scope_of_cited_figure") or ""), "reason": str(data.get("reason") or "")}


_SIZE_SENTENCE = re.compile(r"[^.]*?(?:USD|US\$|\$)\s*\d[\d,]*(?:\.\d+)?\s*(?:Bn|Mn|billion|million)\b[^.]*?(?:\.(?!\d)|$)", re.IGNORECASE)
_STOP = {"and", "other", "others", "etc", "the", "of", "for", "systems", "applications", "application", "&"}


_MONEY = re.compile(r"(?:USD|US\$)\s*\d[\d,]*(?:\.\d+)?\s*(?:Bn|Mn|billion|million)\b", re.IGNORECASE)


def best_size_window(text: str) -> str | None:
    """The text around the money figure most likely to be the base-year market
    size: near '2025'/'2026' and 'valued'/'size', away from 2033 projections."""
    best, best_score = None, None
    for m in _MONEY.finditer(text):
        before = text[max(0, m.start() - 160):m.start()]
        window = (before + m.group(0) + text[m.end():m.end() + 40]).strip()
        low = before.lower()[-90:] + " " + text[m.end():m.end() + 25].lower()
        score = (2 * ("2026" in low or "2025" in low) + ("valued" in low or "size" in low or "stand at" in low)
                 - 3 * ("projection" in low or "2033" in low or "by 20" in low))
        if best_score is None or score > best_score:
            best, best_score = window, score
    return best


_CMI_CACHE: dict[str, list[str]] = {}


def cmi_report_snippets(neon_url: str, segment: str, product_noun: str, limit: int = 30) -> list[str]:
    """CMI's own reports on neighbouring markets of the same product ('Medical
    Laser Systems', 'Directed Energy Weapons', 'Laser Diode', ...): one stated
    market-size sentence per report. All of them are offered for every segment;
    the sizing LLM decides which belong to which segment (a 'Surgical Lasers'
    report belongs to Medical without containing the word)."""
    import psycopg
    noun = product_noun.lower().rstrip("s")
    if not neon_url or len(noun) < 3:
        return []
    if noun not in _CMI_CACHE:
        with psycopg.connect(neon_url) as conn, conn.cursor() as cur:
            sql = ("SELECT keyword, coalesce(\"newsLDesc\", '') || ' ' || coalesce(summary, '') "
                   "FROM cmi_reports WHERE lower(keyword) LIKE %s")
            cur.execute(sql, [f"%{noun}%"])
            rows = cur.fetchall()
        out = []
        for keyword, text in rows:
            plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>|&[a-z]+;", " ", text or ""))
            best = best_size_window(plain)
            if best and keyword:
                out.append(f"CMI report '{keyword}': {best}")
        _CMI_CACHE[noun] = out
    return _CMI_CACHE[noun][:limit]


def segment_definition(nesting: dict[str, Any], dim: str, segment: str) -> str:
    """What the taxonomy itself says the segment contains, plus the market's
    product types -- the sizer must size THAT, not its own narrower idea
    (seen: 'industrial' read as niche high-power use, $1.0B, although the
    taxonomy defines it as cutting, welding, marking, ... with CO2/fiber lasers)."""
    parts = []
    kids = (nesting.get(dim) or {}).get(segment) or []
    if kids:
        parts.append(f"{segment} includes: {', '.join(kids)}")
    for other, tree in nesting.items():
        if other != dim:
            roots = tree.get("__root__") or []
            parts.append(f"{other}: {', '.join(roots[:8])}")
    return "; ".join(parts[:5])


ADJUST_BOUNDS = (0.2, 1.5)
MIN_PUBLISHERS = 2


def _publisher_key(name: str) -> str:
    name = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())
    name = re.sub(r"\b(inc|ltd|llc|pvt|research|insights|reports?|consulting|group|market|markets|the)\b", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def segment_market(llm: Any, market: str, segment: str, search: Callable[[list[str]], list[str]],
                   extra: Callable[[str], list[str]] | None = None, definition: str = "") -> dict[str, Any]:
    """Size one end-use segment from the CONSENSUS of independent publishers.

    Market-research firms define and size markets differently, so no single
    firm's figure is taken as the answer (that would bias the estimate toward
    whichever firm the search happened to surface):
      1. three differently worded searches + CMI's own neighbouring reports
      2. the LLM lists EVERY figure it finds -- publisher, value converted to
         2025, verbatim quote, and how the figure's scope compares with the
         taxonomy's definition of the segment (adjustment factor)
      3. code keeps a figure only if its number is literally in the snippet,
         keeps ONE figure per publisher (CMI is one publisher like any other),
         and takes the MEDIAN of the scope-adjusted figures
    Two or more publishers -> consensus (grounded). One -> single-source,
    flagged, not counted as grounded. None -> the LLM's estimate, flagged."""
    term = product_term(market)
    web = search([f"{segment} {term} market size 2025 USD billion",
                  f"global {term} {segment} market size 2024 2025 CAGR",
                  f"{term} market {segment} segment revenue share 2024"])
    snippets = web[:14] + list(extra(segment) if extra else [])[:25]
    block = "\n".join(f"[{i}] {s}" for i, s in enumerate(snippets)) or "(none)"
    data = llm.complete_json(
        "You list market-size evidence for one end-use segment of a product market, WITHOUT choosing a winner: "
        "market-research firms disagree, and the answer is their consensus, not any one firm's number. For EVERY "
        "distinct figure in the snippets that measures this segment's use of this product, or a market that "
        "overlaps it, give the publisher, the value converted to 2025 (use the stated CAGR to move a 2024 or 2026 "
        "figure), the verbatim sentence, and an adjustment factor that converts the figure's scope to the "
        "segment AS THE TAXONOMY DEFINES IT (1.0 = same scope; below 1 if the figure covers more, e.g. complete "
        "machines or a wider market; above 1 if it covers less). Size the value of the PRODUCT itself, not of "
        "machines or services containing it. Also give your own independent estimate. Return json.",
        f"Segment: {segment} use of {term} (part of the {market})\n"
        f"How the market's taxonomy defines it: {definition or '(no further detail)'}\n\n"
        f"Snippets:\n{block}\n\n"
        'Return JSON: {"figures": [{"publisher": "<firm or source>", "value_usd_mn_2025": <number>, '
        '"quote": "<verbatim sentence>", "snippet_index": <int>, "scope": "<same|broader|narrower>", '
        '"adjustment": <factor>, "why": "<short>"}], "estimate_usd_mn_2025": <your own estimate>, '
        '"cagr": <decimal or null>}',
    )
    accepted: dict[str, dict[str, Any]] = {}
    rejected = 0
    for f in data.get("figures") or []:
        try:
            value = float(f.get("value_usd_mn_2025"))
            adj = float(f.get("adjustment") or 1.0)
        except (TypeError, ValueError):
            rejected += 1
            continue
        idx = f.get("snippet_index")
        snippet = snippets[idx] if isinstance(idx, int) and 0 <= idx < len(snippets) else ""
        quote = str(f.get("quote") or "")
        stated = figures_in(quote)
        # the number must be in the source text (allowing a one-year move at up to ~25% growth)
        if not (value > 0 and quote and quote[:40] in snippet
                and any(0.75 * s <= value <= 1.3 * s for s in stated)):
            rejected += 1
            continue
        raw_pub = str(f.get("publisher") or "").strip()
        pub = _publisher_key(raw_pub)
        if snippet.startswith("CMI report"):
            pub = "coherent market insights"
        named = bool(pub) and not pub.startswith("unknown") and "snippet" not in pub
        adj = min(max(adj, ADJUST_BOUNDS[0]), ADJUST_BOUNDS[1]) if adj > 0 else 1.0
        # independent evidence = a NAMED publisher measuring this segment itself (scope ~same).
        # A whole-market figure the model scaled down is the model's share judgement, not
        # segment evidence: it stays an artifact but never counts toward the consensus.
        independent = named and 0.7 <= adj <= 1.3
        key = pub if named else f"unattributed {idx}"
        if any(abs(x["value_mn"] - value) <= 0.02 * value for x in accepted.values()):
            continue  # the same figure syndicated across snippets/segments counts once
        if key not in accepted:  # one figure per publisher -- no firm counts twice
            accepted[key] = {"publisher": (raw_pub or "unattributed")[:60], "value_mn": value, "adjustment": adj,
                             "adjusted_mn": value * adj, "scope": str(f.get("scope") or ""), "quote": quote[:220],
                             "independent": independent}
    try:
        estimate = float(data.get("estimate_usd_mn_2025"))
    except (TypeError, ValueError):
        estimate = None
    artifacts = list(accepted.values())
    figs = [x for x in artifacts if x["independent"]]
    if len(figs) >= MIN_PUBLISHERS:
        vals = sorted(x["adjusted_mn"] for x in figs)
        n = len(vals)
        median = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2
        return {"segment": segment, "value_mn": median, "grounded": True, "basis": "consensus",
                "n_publishers": n, "spread_mn": [vals[0], vals[-1]], "figures": artifacts, "rejected": rejected,
                "estimate_mn": estimate, "quote": "; ".join(f"{x['publisher']}: {x['quote'][:90]}" for x in figs[:3]),
                "cagr": data.get("cagr"), "reason": f"median of {n} publishers (scope-adjusted)"}
    if len(figs) == 1:
        x = figs[0]
        value = x["adjusted_mn"] if not estimate else (x["adjusted_mn"] * estimate) ** 0.5
        return {"segment": segment, "value_mn": value, "grounded": False, "basis": "single_source",
                "n_publishers": 1, "spread_mn": [x["adjusted_mn"], x["adjusted_mn"]], "figures": artifacts,
                "rejected": rejected, "estimate_mn": estimate, "quote": f"{x['publisher']}: {x['quote'][:120]}",
                "cagr": data.get("cagr"),
                "reason": "one publisher only -- geometric mean with the model's own estimate, flagged single-source"}
    if not estimate or estimate <= 0:  # a missing segment must never count as $0
        try:
            retry = llm.complete_json(
                "Give your best estimate of one market segment's 2025 value. Return json.",
                f"{segment} use of {term} ({definition or market}), global, 2025, in US$ millions.\n"
                'Return JSON: {"estimate_usd_mn_2025": <number>, "reason": "<one sentence>"}')
            estimate = float(retry.get("estimate_usd_mn_2025"))
        except Exception:  # noqa: BLE001
            estimate = None
    derived = sorted(x["adjusted_mn"] for x in artifacts)
    return {"segment": segment, "value_mn": estimate, "grounded": False,
            "basis": "estimate" if not derived else "estimate_with_derived_artifacts", "n_publishers": 0,
            "spread_mn": [derived[0], derived[-1]] if derived else None, "figures": artifacts, "rejected": rejected,
            "estimate_mn": estimate, "quote": "",
            "cagr": data.get("cagr"), "reason": "no verifiable publisher figure -- model estimate"}


def reconcile(llm: Any, market: str, nesting: dict[str, Any], anchor_mn: float, cited: str,
              search: Callable[[list[str]], list[str]], extra: Callable[[str], list[str]] | None = None) -> dict[str, Any] | None:
    dim = partition_dimension(nesting)
    if not dim or not cited:
        return None
    segments = list((nesting.get(dim) or {}).get("__root__") or [])
    if len(segments) < 2:
        return None
    cov = judge_coverage(llm, market, cited, segments)
    if not cov["uncovered"] or not cov["covered"]:
        return {"dimension": dim, **cov, "anchor_mn": anchor_mn, "changed": False, "components": []}
    comps = [segment_market(llm, market, s, search, extra, segment_definition(nesting, dim, s))
             for s in cov["uncovered"]]
    added = sum(c["value_mn"] or 0.0 for c in comps)
    return {"dimension": dim, **cov, "cited_anchor_mn": anchor_mn, "anchor_mn": anchor_mn + added,
            "changed": added > 0, "components": comps,
            "grounded_share_of_added": (sum(c["value_mn"] or 0 for c in comps if c["grounded"]) / added) if added else 0.0}


def bottom_up(llm: Any, market: str, nesting: dict[str, Any], anchor_mn: float,
              search: Callable[[list[str]], list[str]], extra: Callable[[str], list[str]] | None = None) -> dict[str, Any] | None:
    """No citable total: size every end-use segment on its own (evidence-first)
    and use the sum -- one unsourced top-down guess tends to carry a narrow
    idea of the market (seen: $4.5B 'high energy laser' vs a taxonomy that
    spans industrial, medical, sensing ... lasers)."""
    dim = partition_dimension(nesting)
    segments = list((nesting.get(dim) or {}).get("__root__") or []) if dim else []
    if len(segments) < 2:
        return None
    comps = [segment_market(llm, market, s, search, extra, segment_definition(nesting, dim, s)) for s in segments]
    total = sum(c["value_mn"] or 0.0 for c in comps)
    if total <= 0 or any(not c["value_mn"] for c in comps):  # incomplete -> not a valid whole-market total
        return None
    return {"dimension": dim, "mode": "bottom_up", "covered": [], "uncovered": segments, "scope": "model estimate",
            "reason": "no citable total -- whole market built from its end-use segments",
            "cited_anchor_mn": anchor_mn, "anchor_mn": total, "changed": True, "components": comps,
            "grounded_share_of_added": sum(c["value_mn"] or 0 for c in comps if c["grounded"]) / total}


def brief(rec: dict[str, Any] | None) -> str:
    """Evidence text for the shares agent: the market's composition by end use."""
    if not rec or not rec.get("changed"):
        return ""
    if rec.get("mode") == "bottom_up":
        lines = [f"SCOPE RECONCILIATION ({rec['dimension']}): no citable total; whole market built from its "
                 f"end-use segments = ${rec['anchor_mn']:,.0f}M:"]
    else:
        lines = [f"SCOPE RECONCILIATION ({rec['dimension']}): the cited ${rec['cited_anchor_mn']:,.0f}M figure covers only "
                 f"{', '.join(rec['covered'])} ({rec['scope']}). Whole market = ${rec['anchor_mn']:,.0f}M:"]
        lines.append(f"- {', '.join(rec['covered'])}: ${rec['cited_anchor_mn']:,.0f}M (cited)")
    for c in rec["components"]:
        basis = c.get("basis") or ("consensus" if c["grounded"] else "estimate")
        tag = (f"median of {c['n_publishers']} publishers, range ${c['spread_mn'][0]:,.0f}-{c['spread_mn'][1]:,.0f}M"
               if basis == "consensus" else "one publisher only" if basis == "single_source" else "estimate")
        lines.append(f"- {c['segment']}: ${c['value_mn'] or 0:,.0f}M ({tag})")
    return "\n".join(lines)
