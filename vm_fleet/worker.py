"""Runs on each VM: pulls markets from a shared GCS queue and builds their CMI whitepapers,
`--workers` markets at a time. Standard library + the gcloud CLI only.

Queue: a market is claimed by creating gs://<bucket>/claims/<slug>.json with
--if-generation-match=0, which succeeds for exactly one VM. Any number of VMs can
share one market list; a VM that restarts skips what is already claimed.

Per market: run_fleet.py (with the workbook's definition as the client anchor) ->
run_cmi_report.py --no-word -> PDF via LibreOffice (if installed) -> upload
AiAgentsFleet/output/<slug>/ to gs://<bucket>/output/<slug>/ -> write
gs://<bucket>/status/<slug>.json. run_cmi_report exit code 0 = READY, 3 = DRAFT
(built, failed the release gate), anything else = failed.

    python vm_fleet/worker.py --bucket B --markets markets.jsonl --root /opt/wp/code --workers 6 [--only slug1,slug2]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import market_json  # noqa: E402

HOST = socket.gethostname()
FLEET_TIMEOUT = 2 * 3600       # seconds per step; a hung API call must not hold a worker forever
REPORT_TIMEOUT = 4 * 3600
_print_lock = threading.Lock()
STOP = threading.Event()        # set on a search-quota error: no worker claims another market


def log(msg: str) -> None:
    with _print_lock:
        print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} [{HOST}] {msg}", flush=True)


def gcs(*args: str, check: bool = True, input_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["gcloud", "storage", *args], capture_output=True, text=True, check=check,
                          input=input_text)


def put_json(url: str, data: dict, *, if_absent: bool = False) -> bool:
    """Writes `data` to a GCS object; with if_absent, only when the object doesn't exist yet."""
    tmp = Path(f"/tmp/{uuid.uuid4().hex}.json")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        r = gcs("cp", *(["--if-generation-match=0"] if if_absent else []), str(tmp), url, check=False)
        return r.returncode == 0
    finally:
        tmp.unlink(missing_ok=True)


def run_step(cmd: list[str], logfile: Path, cwd: Path, timeout: int, extra_env: dict | None = None) -> int:
    with logfile.open("a", encoding="utf-8") as fh:
        fh.write(f"\n$ {' '.join(cmd)}\n")
        fh.flush()
        try:
            return subprocess.run(cmd, cwd=cwd, stdout=fh, stderr=subprocess.STDOUT, timeout=timeout,
                                  env={**os.environ, "PYTHONUNBUFFERED": "1", "MPLBACKEND": "Agg",
                                       **(extra_env or {})}).returncode
        except subprocess.TimeoutExpired:
            fh.write(f"\n!! timed out after {timeout}s\n")
            return 124


def to_pdf(docx: Path, slug: str) -> None:
    """Fallback only: run_cmi_report --no-word already writes the PDF (with real TOC page numbers)."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice or not docx.exists() or docx.with_suffix(".pdf").exists():
        return
    profile = f"file:///tmp/lo_{slug}"          # one profile per process: parallel soffice runs collide otherwise
    subprocess.run([soffice, f"-env:UserInstallation={profile}", "--headless", "--convert-to", "pdf",
                    "--outdir", str(docx.parent), str(docx)], capture_output=True, timeout=600)


def build_market(m: dict, root: Path, bucket: str) -> dict:
    slug, market = m["slug"], m["market"]
    mdir = root / "AiAgentsFleet" / "output" / slug
    mdir.mkdir(parents=True, exist_ok=True)
    logfile = mdir / "run.log"
    t0 = time.time()
    py = sys.executable
    status: dict = {"slug": slug, "market": market, "host": HOST, "started": datetime.now(timezone.utc).isoformat()}

    # every search of this market logged; one cache shared by all VMs and markets; a quota error stops the run
    # the cost configuration tested 2026-10-21 (~$0.13 Perplexity + ~$0.09 DeepSeek per market): fast search,
    # free page text for vendor checks, free-first source-name checks, every search and LLM call logged
    senv = {"SEARCH_LOG": str(mdir / "searches.jsonl"), "LLM_LOG": str(mdir / "llm_calls.jsonl"),
            "SEARCH_SHARED_CACHE": f"gs://{bucket}/search-cache", "WEB_SEARCH_STRICT": "1",
            "WEB_SEARCH_MODE": "fast", "ENTITY_PAGE_FETCH": "1", "NAME_CHECK_FREE_FIRST": "1"}
    # a market that failed here before (e.g. out of LLM credit) left error answers in its saved data: start it fresh
    prev = mdir / "vm_status.json"
    try:
        prev_result = json.loads(prev.read_text(encoding="utf-8")).get("result") if prev.exists() else None
    except ValueError:
        prev_result = None
    if prev_result in ("failed", "quota"):
        for f in ("whitepaper_fields.json", "cmi_fields.json", "cmi_prose.json", "report_extras.json", "resources.json"):
            if (mdir / f).exists():
                (mdir / f).rename(mdir / (f + ".failed-run"))
    rc_fleet = 0
    if not (mdir / "whitepaper_fields.json").exists():
        dfile = mdir / "client_definition.json"
        dfile.write_text(json.dumps(m["definition"], ensure_ascii=False, indent=2), encoding="utf-8")
        rc_fleet = run_step([py, "AiAgentsFleet/run_fleet.py", "--market", market, "--groups", "cmi",
                             "--definition-file", str(dfile)],
                            logfile, root, FLEET_TIMEOUT, senv)
    status["fleet_rc"] = rc_fleet
    rc = run_step([py, "AiAgentsFleet/cmi_report/run_cmi_report.py", "--market-slug", slug, "--no-word"],
                  logfile, root, REPORT_TIMEOUT, senv) if rc_fleet == 0 else rc_fleet
    status["report_rc"] = rc
    status["result"] = "ready" if rc == 0 else "draft" if rc == 3 else "failed"
    log_text = logfile.read_text(encoding="utf-8", errors="replace")
    if rc not in (0, 3) and ("PERPLEXITY_QUOTA_EXHAUSTED" in log_text or "DEEPSEEK_BALANCE_EXHAUSTED" in log_text):
        status["result"] = "quota"          # out of search or LLM credit: released, the VM stops claiming
    for name in (f"{slug}-cmi-whitepaper.docx", f"{slug}-cmi-whitepaper.DRAFT.docx"):
        to_pdf(mdir / name, slug)
    status["minutes"] = round((time.time() - t0) / 60, 1)
    status["finished"] = datetime.now(timezone.utc).isoformat()
    status.update(market_cost(mdir))
    (mdir / "vm_status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
    # saved the moment it exists, before anything else: the market's JSON, then its logs, then the rest
    try:                                          # the one-file JSON of the market (market_json.py)
        mj = market_json.build(mdir, {k: m.get(k) for k in ("order", "sheet", "row", "market")} | {"host": HOST})
        r = gcs("cp", str(mj), f"gs://{bucket}/json/{slug}.json", check=False)
        status["json_saved"] = r.returncode == 0
    except Exception as exc:  # noqa: BLE001 - the report itself is done; the JSON can be rebuilt later
        status["json_error"] = repr(exc)
    for f in ("run.log", "searches.jsonl", "llm_calls.jsonl", "vm_status.json"):
        if (mdir / f).exists():
            gcs("cp", str(mdir / f), f"gs://{bucket}/output/{slug}/{f}", check=False)
    gcs("rsync", "--recursive", str(mdir), f"gs://{bucket}/output/{slug}", check=False)
    put_json(f"gs://{bucket}/status/{slug}.json", status)
    return status


def market_cost(mdir: Path) -> dict:
    """What this market cost, from its own logs: paid Perplexity searches and DeepSeek tokens."""
    out = {"perplexity_usd": 0.0, "deepseek_usd": 0.0, "searches_paid": 0, "searches_cached": 0, "searches_free": 0,
           "llm_calls": 0}
    try:
        for ln in (mdir / "searches.jsonl").read_text(encoding="utf-8").splitlines():
            r = json.loads(ln)
            if r["source"] == "paid":
                out["searches_paid"] += 1
                out["perplexity_usd"] += 0.005 if r["mode"] == "standard" else 0.001
            elif r["source"] == "ddgs":
                out["searches_free"] += 1
            else:
                out["searches_cached"] += 1
    except (OSError, ValueError, KeyError):
        pass
    try:
        for ln in (mdir / "llm_calls.jsonl").read_text(encoding="utf-8").splitlines():
            u = json.loads(ln)
            out["llm_calls"] += 1
            out["deepseek_usd"] += (u["in_hit"] * 0.003 + u["in_miss"] * 0.15 + u["out"] * 0.60) / 1e6   # off-peak
    except (OSError, ValueError, KeyError):
        pass
    out["perplexity_usd"] = round(out["perplexity_usd"], 4)
    out["deepseek_usd"] = round(out["deepseek_usd"], 4)
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--bucket", required=True)
    p.add_argument("--markets", required=True, help="markets.jsonl from prepare_markets.py")
    p.add_argument("--root", required=True, help="the repo root (holds AiAgentsFleet/ and src/)")
    p.add_argument("--workers", type=int, default=6, help="markets built at the same time on this VM")
    p.add_argument("--only", default="", help="comma-separated slugs: run just these")
    a = p.parse_args()
    root = Path(a.root)
    markets = [json.loads(x) for x in Path(a.markets).read_text(encoding="utf-8").splitlines() if x.strip()]
    if a.only:
        want = [s.strip() for s in a.only.replace(";", ",").split(",") if s.strip()]   # ';' from VM metadata
        markets = [m for m in markets if m["slug"] in want]
    log(f"{len(markets)} market(s) in the queue, {a.workers} worker(s)")

    queue = iter(markets)
    qlock = threading.Lock()
    done: list[dict] = []

    def claim_next() -> dict | None:
        while True:
            with qlock:
                m = next(queue, None)
            if m is None:
                return None
            if put_json(f"gs://{a.bucket}/claims/{m['slug']}.json",
                        {"host": HOST, "claimed": datetime.now(timezone.utc).isoformat()}, if_absent=True):
                return m

    def worker(n: int) -> None:
        time.sleep(n * 5)                        # stagger the first searches
        while not STOP.is_set() and (m := claim_next()) is not None:
            log(f"w{n} start  #{m['order']} {m['market']}")
            try:
                s = build_market(m, root, a.bucket)
            except Exception as exc:  # noqa: BLE001 - one market never stops the worker
                s = {"slug": m["slug"], "result": "failed", "error": repr(exc)}
                put_json(f"gs://{a.bucket}/status/{m['slug']}.json", s)
            if s.get("result") == "quota":
                # out of search credit: release the market for a later run and stop claiming on this VM
                for obj in ("claims", "status"):          # one object per call (see launch.reset-failed)
                    gcs("rm", f"gs://{a.bucket}/{obj}/{m['slug']}.json", check=False)
                STOP.set()
                log(f"w{n} PERPLEXITY QUOTA EXHAUSTED on {m['market']}: released it, stopping this VM's workers")
                continue
            done.append(s)
            log(f"w{n} {s['result'].upper():6} #{m['order']} {m['market']} ({s.get('minutes', '?')} min)")

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        list(ex.map(worker, range(a.workers)))
    # second pass: markets released while this VM was working through the list (launch.py reset-failed)
    # have no claim again -- pick them up before powering off
    queue = iter(markets)
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        list(ex.map(worker, range(a.workers)))
    counts = {k: sum(1 for s in done if s["result"] == k) for k in ("ready", "draft", "failed")}
    log(f"queue empty: {len(done)} built here -- {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
