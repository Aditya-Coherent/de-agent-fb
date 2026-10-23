"""Head-to-head search-backend benchmark on real queries from one market run, scored with the pipeline's
own evidence checks (sources.py, entities.named_in). Perplexity is read from the local search cache (free);
the others are queried live.

    python vm_fleet/search_benchmark.py <slug> [--n 30] [--searxng https://instance] [--out results.json]

Backends:
  perplexity  cached results of the run (what the pipeline used)
  ddgs        free metasearch (Bing/DuckDuckGo/Google/Mojeek) -- the same kind of engine as SearXNG,
              which Vane (ex-Perplexica) and Morphic run on
  searxng     a SearXNG instance's JSON API, if one is given and answers
"""
from __future__ import annotations

import argparse
import json
import random
import re
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from me_engine.generation import web_evidence as w  # noqa: E402

from AiAgentsFleet.cmi_report.entities import named_in  # noqa: E402
from AiAgentsFleet.sources import filter_sources, on_topic, quality  # noqa: E402

N_RESULTS = 8


def _shape(raw: list[dict]) -> list[dict]:
    out = []
    for r in raw:
        title = (r.get("title") or "").strip()
        body = " ".join((r.get("snippet") or r.get("content") or "").split())
        url = (r.get("url") or r.get("href") or "").strip()
        if body:
            text = f"{title}: {body}" if title else body
            out.append({"text": text, "url": url, "domain": w._domain(url), "title": title})
    return out


def perplexity_cached(q: str, mode: str) -> list[dict] | None:
    hit = w._cache_get(w._cache_key("perplexity", mode, w._PPLX_TOKENS_PER_PAGE, w._FETCH_N, w.normalise_query(q)))
    return _shape(hit[:N_RESULTS]) if hit is not None else None


def ddgs(q: str, mode: str) -> list[dict]:
    return _shape(w._ddgs_results(q, N_RESULTS))


def searxng(base: str):
    import httpx

    def run(q: str, mode: str) -> list[dict] | None:
        try:
            r = httpx.get(f"{base.rstrip('/')}/search", params={"q": q, "format": "json"}, timeout=20,
                          headers={"User-Agent": "Mozilla/5.0 (benchmark)"})
            if r.status_code != 200:
                return None
            return _shape(r.json().get("results", [])[:N_RESULTS])
        except Exception:  # noqa: BLE001
            return None
    return run


def score(results: list[dict] | None, q: str, kind: str, market: str) -> dict | None:
    if results is None:
        return None
    kept, _ = filter_sources(results)
    s = {"n": len(kept), "chars": statistics.mean([len(r["text"]) for r in kept]) if kept else 0,
         "trusted": quality(kept)["trusted"] if kept else 0}
    if kind == "market":
        s["on_topic"] = len(on_topic(kept, market)[0]) / len(kept) if kept else 0.0
    if kind == "company":
        name = re.sub(r"\s+headquarters parent company owned by$", "", q)
        s["names_company"] = sum(1 for r in kept if named_in(r["text"], name))
    return s


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("slug")
    p.add_argument("--n", type=int, default=30)
    p.add_argument("--searxng", default="")
    p.add_argument("--out")
    a = p.parse_args()
    d = ROOT / "AiAgentsFleet" / "output" / a.slug
    market = json.loads((d / "whitepaper_fields.json").read_text(encoding="utf-8"))["market_name"]
    rows = [json.loads(x) for x in (d / "searches.jsonl").read_text(encoding="utf-8").splitlines()]
    paid = list({(r["mode"], r["query"]): r for r in rows if r["source"] == "paid"}.values())
    company = [r for r in paid if r["query"].endswith("headquarters parent company owned by")]
    standard = [r for r in paid if r["mode"] == "standard"]
    marketq = [r for r in paid if market.lower() in r["query"].lower() and r["mode"] == "fast"]
    rnd = random.Random(7)
    k = a.n // 3
    sample = ([(r, "market") for r in rnd.sample(marketq, min(k, len(marketq)))]
              + [(r, "company") for r in rnd.sample(company, min(k, len(company)))]
              + [(r, "precise") for r in rnd.sample(standard, min(a.n - 2 * k, len(standard)))])
    backends = {"perplexity": perplexity_cached, "ddgs": ddgs}
    if a.searxng:
        backends["searxng"] = searxng(a.searxng)
    res: dict[str, list] = {b: [] for b in backends}
    for i, (r, kind) in enumerate(sample, 1):
        for b, fn in backends.items():
            t0 = time.time()
            out = fn(r["query"], r["mode"])
            sc = score(out, r["query"], kind, market)
            res[b].append({"query": r["query"], "kind": kind, "secs": round(time.time() - t0, 2), **(sc or {"failed": True})})
            if b != "perplexity":
                time.sleep(1.5)                       # be polite to free engines
        print(f"{i}/{len(sample)} {kind:8s} {r['query'][:70]}", flush=True)
    print(f"\n{'backend':12s} {'kind':8s} {'queries':>7s} {'failed':>6s} {'results':>7s} {'chars':>6s} "
          f"{'trusted':>7s} {'on-topic':>8s} {'co.hits':>7s} {'secs':>5s}")
    for b, items in res.items():
        for kind in ("market", "company", "precise", "ALL"):
            its = [x for x in items if kind == "ALL" or x["kind"] == kind]
            ok = [x for x in its if not x.get("failed")]
            m = lambda key: statistics.mean([x[key] for x in ok if key in x]) if any(key in x for x in ok) else float("nan")  # noqa: E731
            print(f"{b:12s} {kind:8s} {len(its):7d} {len(its) - len(ok):6d} {m('n'):7.1f} {m('chars'):6.0f} "
                  f"{m('trusted'):7.1f} {m('on_topic'):8.0%} {m('names_company'):7.1f} {m('secs'):5.1f}")
    if a.out:
        Path(a.out).write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
