"""Watches a fleet run from the local machine; one stdout line per event worth acting on.

    python vm_fleet/monitor.py [--interval 60] [--summary-every 10]

Every --interval seconds it:
  * saves every finished market's JSON (gs://<bucket>/json -> vm_fleet/json/) and its run log
    (-> vm_fleet/logs/markets/<slug>.run.log), so nothing depends on the VMs staying up;
  * prints a line for each market that failed or came out DRAFT (with its blockers), each READY market
    whose log still shows errors (tracebacks, failed LLM/search calls, timeouts), each market claimed
    more than --stuck-min minutes ago with no result, and each VM that changes state;
  * prints a progress summary every --summary-every minutes, and a final line when the queue is done.
Everything printed is also appended to vm_fleet/logs/monitor.log. State is kept in
vm_fleet/logs/monitor_state.json, so a restarted monitor does not repeat old events.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from launch import BUCKET, PROJECT, gcloud_bin  # noqa: E402

LOGS = HERE / "logs"
MARKET_LOGS = LOGS / "markets"
JSON_DIR = HERE / "json"
STATE = LOGS / "monitor_state.json"
# lines in a market's run.log that mean something went wrong, even in a READY report
# ('connection driver error' is httpx/ddgs retry noise, not a pipeline error)
ERROR_RE = re.compile(r"Traceback|LLM failed|LLM call failed|failed after \d+ attempts|timed out|no answer within|"
                      r"cut off at the|search failed|re-ask failed|\b[A-Z]\w*Error:|Killed|MemoryError")


def gs(*args: str) -> str:
    r = subprocess.run([gcloud_bin(), *args, f"--project={PROJECT}"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stdout if r.returncode == 0 else ""


def now() -> datetime:
    return datetime.now(timezone.utc)


def emit(msg: str) -> None:
    line = f"{now():%H:%M:%S} {msg}"
    print(line, flush=True)
    with (LOGS / "monitor.log").open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def listing(prefix: str) -> dict[str, datetime]:
    """{slug: object time} for gs://<bucket>/<prefix>/*.json."""
    out = {}
    for ln in gs("storage", "ls", "-l", f"gs://{BUCKET}/{prefix}/").splitlines():
        m = re.match(r"\s*\d+\s+(\S+)\s+gs://.+/([^/]+)\.json$", ln)
        if m:
            out[m.group(2)] = datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--interval", type=int, default=60)
    p.add_argument("--summary-every", type=int, default=10, help="minutes between progress summaries")
    p.add_argument("--stuck-min", type=int, default=25)
    a = p.parse_args()
    MARKET_LOGS.mkdir(parents=True, exist_ok=True)
    JSON_DIR.mkdir(parents=True, exist_ok=True)
    st = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    seen: dict[str, str] = st.get("seen", {})            # slug -> result already reported
    seen_at: dict[str, str] = st.get("seen_at", {})      # slug -> time of the status reported
    costs: dict[str, list] = st.get("costs", {})         # slug -> [perplexity $, deepseek $, minutes]
    stuck_told: set[str] = set(st.get("stuck_told", []))
    vm_state: dict[str, str] = st.get("vm_state", {})
    total = sum(1 for _ in (HERE / "markets.jsonl").open(encoding="utf-8"))
    last_summary = 0.0
    t0 = st.get("t0") or now().isoformat()
    emit(f"monitor started: {len(seen)} market(s) already recorded, {total} in the queue")

    while True:
        done = listing("status")
        claims = listing("claims")
        for slug in [k for k in seen if k not in done]:            # released for a retry
            seen.pop(slug); seen_at.pop(slug, None); stuck_told.discard(slug)
        # a market is new when its status is newer than the one reported (a released market that was
        # rebuilt writes a fresh status)
        new = [s for s in done if seen_at.get(s) != done[s].isoformat()]
        if new:
            gs("storage", "rsync", f"gs://{BUCKET}/json", str(JSON_DIR))
        for slug in new:
            raw = gs("storage", "cat", f"gs://{BUCKET}/status/{slug}.json")
            try:
                s = json.loads(raw)
            except json.JSONDecodeError:
                continue                                  # written mid-read: next round
            logf = MARKET_LOGS / f"{slug}.run.log"
            gs("storage", "cp", f"gs://{BUCKET}/output/{slug}/run.log", str(logf))
            text = logf.read_text(encoding="utf-8", errors="replace") if logf.exists() else ""
            blockers = [ln.strip()[8:] for ln in text.splitlines() if ln.strip().startswith("BLOCKER")]
            errs = sorted({ERROR_RE.search(ln).group(0).lower() for ln in text.splitlines() if ERROR_RE.search(ln)})
            res = s.get("result", "?")
            if res in ("ready", "draft") and (s.get("json_saved") is False or s.get("json_error")):
                emit(f"JSON NOT SAVED {slug}: {s.get('json_error') or 'upload failed'}")
            host = (s.get("host") or "?").split(".")[0]
            if res == "failed":
                tail = " | ".join(ln.strip() for ln in text.splitlines()[-4:])[:300]
                emit(f"FAILED {slug} on {host} after {s.get('minutes')} min: {s.get('error') or tail}")
            elif res == "draft":
                emit(f"DRAFT  {slug} ({s.get('minutes')} min): " + "; ".join(b[:160] for b in blockers))
            elif errs:
                emit(f"READY-with-errors {slug}: {', '.join(errs)}")
            else:
                with (LOGS / "monitor.log").open("a", encoding="utf-8") as fh:   # logged, not an event
                    fh.write(f"{now():%H:%M:%S} READY  {slug} ({s.get('minutes')} min, {host})\n")
            seen[slug] = res
            seen_at[slug] = done[slug].isoformat()
            if "perplexity_usd" in s:                    # measured on the VM from the market's own logs
                costs[slug] = [s.get("perplexity_usd", 0), s.get("deepseek_usd", 0), s.get("minutes", 0)]
        # claimed long ago, no result yet
        for slug, t in claims.items():
            if slug not in done and slug not in stuck_told and (now() - t).total_seconds() > a.stuck_min * 60:
                emit(f"STUCK? {slug} claimed {int((now() - t).total_seconds() // 60)} min ago, no result yet")
                stuck_told.add(slug)
        # VM state changes
        vms = dict(ln.split()[:2] for ln in gs("compute", "instances", "list", "--filter=labels.app=whitepaper",
                                                 "--format=value(name,status)").splitlines() if ln.strip())
        for name, status in vms.items():
            if vm_state.get(name) != status:
                emit(f"VM {name}: {vm_state.get(name, 'new')} -> {status}")
        for name in set(vm_state) - set(vms):
            emit(f"VM {name}: deleted")
        vm_state = vms
        # summary
        counts = {k: sum(1 for v in seen.values() if v == k) for k in ("ready", "draft", "failed")}
        running = len([c for c in claims if c not in done])
        if time.time() - last_summary >= a.summary_every * 60:
            mins = (now() - datetime.fromisoformat(t0)).total_seconds() / 60
            pp = sum(c[0] for c in costs.values()); ds = sum(c[1] for c in costs.values()); n = len(costs)
            # rate and cost from this run's markets only (the ones that report a measured cost)
            rate = n / mins if mins > 0 else 0
            left = total - len(seen)
            eta = f", ~{left / rate:.0f} min left (at {rate * 60:.0f} markets/h)" if rate > 0 else ""
            cost = (f"; this run so far ${pp:.2f} Perplexity + ${ds:.2f} DeepSeek over {n} market(s), "
                    f"avg ${pp / n:.3f} + ${ds / n:.3f} -> remaining {left} ~${(pp + ds) / n * left:.0f} "
                    f"(run total ~${pp + ds + (pp + ds) / n * left:.0f})") if n else ""
            emit(f"PROGRESS {len(seen)}/{total} done {counts}, {running} running, "
                 f"{sum(1 for v in vms.values() if v == 'RUNNING')} VM(s) up{eta}{cost}")
            last_summary = time.time()
        STATE.write_text(json.dumps({"seen": seen, "seen_at": seen_at, "costs": costs, "stuck_told": sorted(stuck_told), "vm_state": vm_state,
                                     "t0": t0}, indent=1), encoding="utf-8")
        if len(seen) >= total or (vms and all(v == "TERMINATED" for v in vms.values()) and not running):
            emit(f"QUEUE DONE: {len(seen)}/{total} {counts}; JSONs in {JSON_DIR}, logs in {MARKET_LOGS}")
            return 0
        time.sleep(a.interval)


if __name__ == "__main__":
    raise SystemExit(main())
