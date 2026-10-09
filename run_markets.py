"""Run the next markets from markets.txt through all approaches.

    python run_markets.py --count 6            # next 6 markets, 2 at a time
    python run_markets.py --count 6 --parallel 1

Each market runs `run_examples.py` (Desk, 2A, 2B, Supply). As soon as a
market finishes, its result file, folder and values are printed. A market
whose four approaches all finished ("ok") is moved from markets.txt to
markets_done.txt, so the next call starts with the next markets. A market
with a failed approach stays in markets.txt and is listed at the end.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MARKETS = ROOT / "markets.txt"
DONE = ROOT / "markets_done.txt"
RESULTS = ROOT / "approaches" / "examples"
LOGS = RESULTS / "_logs"
APPROACHES = [
    ("1_desk_research", "1  Desk Research", "global_usd_mn"),
    ("2A_demand_country_total", "2A Country total", "global_usd_mn"),
    ("2B_demand_usecase_country", "2B Use case x country", "global_usd_mn"),
    ("3_supply_side", "3  Supply-Side", "company_method_global_usd_mn"),
]
_lock = threading.Lock()


def slug(name: str) -> str:
    sys.path.insert(0, str(ROOT / "src"))
    from me_engine.demandUseCaseCountry_approach2B.checkpoints import slugify
    return slugify(name)


def read_markets() -> list[tuple[str, str]]:
    out = []
    for line in MARKETS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            market, _, vertical = line.partition("|")
            out.append((market.strip(), vertical.strip()))
    return out


def mark_done(market: str, vertical: str) -> None:
    with _lock:
        lines = MARKETS.read_text(encoding="utf-8").splitlines()
        keep = [l for l in lines if l.partition("|")[0].strip() != market]
        MARKETS.write_text("\n".join(keep) + "\n", encoding="utf-8")
        with DONE.open("a", encoding="utf-8") as f:
            f.write(f"{market}|{vertical}\n")


def run_one(market: str, vertical: str) -> tuple[str, bool]:
    folder = RESULTS / slug(market)
    log_path = LOGS / f"{slug(market)}.log"
    LOGS.mkdir(parents=True, exist_ok=True)
    started = datetime.now()
    print(f"[{started:%H:%M}] START  {market}  (log: {log_path.relative_to(ROOT)})", flush=True)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.run(
            [sys.executable, "run_examples.py", "--market", market, "--vertical", vertical],
            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
        )
    result_file = folder / "result.json"
    result = json.loads(result_file.read_text(encoding="utf-8")) if result_file.exists() else {}
    all_ok = proc.returncode == 0 and all((result.get(k) or {}).get("status") == "ok" for k, _, _ in APPROACHES)
    minutes = (datetime.now() - started).total_seconds() / 60
    lines = [
        "",
        f"[{datetime.now():%H:%M}] DONE   {market}  ({minutes:.0f} min) -- {'all approaches ok' if all_ok else 'SOME APPROACHES FAILED'}",
        f"   result file : {result_file.relative_to(ROOT)}",
        f"   folder      : {folder.relative_to(ROOT)}",
    ]
    for key, label, field in APPROACHES:
        v = result.get(key) or {}
        value = v.get(field)
        shown = f"${value:,.0f}M" if isinstance(value, (int, float)) else "-"
        lines.append(f"   {label:24} {v.get('status', 'not run'):12} {shown}")
    with _lock:
        print("\n".join(lines) + "\n", flush=True)
    if all_ok:
        mark_done(market, vertical)
    return market, all_ok


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--count", type=int, default=6, help="how many markets to run from the top of markets.txt")
    p.add_argument("--parallel", type=int, default=2, help="markets at the same time (default 2)")
    args = p.parse_args()

    todo = read_markets()[: args.count]
    if not todo:
        print("markets.txt is empty.")
        return 0
    print(f"Running {len(todo)} market(s), {args.parallel} at a time: {', '.join(m for m, _ in todo)}\n", flush=True)
    failed = []
    with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as pool:
        for fut in as_completed([pool.submit(run_one, m, v) for m, v in todo]):
            market, ok = fut.result()
            if not ok:
                failed.append(market)

    subprocess.run([sys.executable, "run_examples.py", "--summary-only"], cwd=ROOT,
                   env={**os.environ, "PYTHONIOENCODING": "utf-8"}, stdout=subprocess.DEVNULL)
    print(f"Summary of all markets: {(RESULTS / 'EXAMPLES_SUMMARY.md').relative_to(ROOT)}  (also .xlsx)")
    if failed:
        print(f"Need a rerun (kept in markets.txt): {', '.join(failed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
