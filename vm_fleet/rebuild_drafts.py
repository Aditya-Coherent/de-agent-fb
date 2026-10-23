"""Rebuilds DRAFT (or named) markets on this machine from the data the VMs saved, report step only.

    python vm_fleet/rebuild_drafts.py                 # every market whose status is "draft"
    python vm_fleet/rebuild_drafts.py --slugs a,b     # these markets
    python vm_fleet/rebuild_drafts.py --refresh-data --slugs x   # also re-run the CMI agents (data blockers)

For each market: gs://<bucket>/output/<slug>/ -> AiAgentsFleet/output/<slug>/, run_cmi_report.py on the saved
agent data (no fleet re-run; cached text is reused and re-guarded), then the market JSON, and everything back
to the bucket with a fresh status (the monitor reports the new result). The fixes that turn a draft READY are
all in the report step, so no search or agent call is repeated unless --refresh-data is passed. Uses MS Word
here for the PDF and real contents-page numbers. Runs one market at a time (Word automation is not parallel).
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import market_json  # noqa: E402
from launch import BUCKET, _ls_json  # noqa: E402
from launch import gc as _gc  # noqa: E402


def gc(*args, check: bool = True, capture: bool = False, tries: int = 3):
    """launch.gc with retries: a network blip (DNS, token refresh) must not end a long rebuild run."""
    for k in range(tries):
        r = _gc(*args, check=False, capture=True)
        if r.returncode == 0 or not check:
            return r
        time.sleep(10 * (k + 1))
    raise RuntimeError(f"gcloud {' '.join(args[:3])} failed after {tries} tries: {(r.stderr or '')[-300:]}")

OUT = ROOT / "AiAgentsFleet" / "output"


def rebuild(slug: str, refresh: bool, market_of: dict, no_word: bool = False, refresh_prose: bool = False,
            force: bool = False) -> dict:
    mdir = OUT / slug
    mdir.mkdir(parents=True, exist_ok=True)
    gc("storage", "rsync", "--recursive", f"gs://{BUCKET}/output/{slug}", str(mdir), capture=True)
    t0 = time.time()
    started = datetime.now(timezone.utc).isoformat()
    log = mdir / "run.log"
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"\n=== local rebuild {started} ({socket.gethostname()}) ===\n")
        fh.flush()
        rc = subprocess.run([sys.executable, "AiAgentsFleet/cmi_report/run_cmi_report.py", "--market-slug", slug,
                             *(["--refresh-data"] if refresh else []), *(["--no-word"] if no_word else []),
                             *(["--refresh-prose"] if refresh_prose else [])],
                            cwd=ROOT, stdout=fh,
                            stderr=subprocess.STDOUT, timeout=3600).returncode
    status = {"slug": slug, "market": market_of.get(slug, {}).get("market"), "host": socket.gethostname() if no_word else f"local:{socket.gethostname()}",
              "started": started, "fleet_rc": 0, "report_rc": rc,
              "result": "ready" if rc == 0 else "draft" if rc == 3 else "failed",
              "minutes": round((time.time() - t0) / 60, 1), "finished": datetime.now(timezone.utc).isoformat(),
              "rebuilt_from_saved_data": not refresh}
    # never downgrade what the bucket holds: a rebuild that comes out worse than the stored result (READY ->
    # DRAFT, anything -> FAILED, e.g. a search refused for lack of credit) keeps the stored files (2026-10-22)
    rank = {"failed": 0, "draft": 1, "ready": 2}
    prev = gc("storage", "cat", f"gs://{BUCKET}/status/{slug}.json", check=False, capture=True)
    try:
        prev_result = json.loads(prev.stdout).get("result") if prev.returncode == 0 else None
    except ValueError:
        prev_result = None
    if not force and prev_result and rank.get(status["result"], 0) < rank.get(prev_result, 0):
        status["kept_previous"] = prev_result
        return status
    (mdir / "vm_status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
    m = market_of.get(slug, {})
    mj = market_json.build(mdir, {k: m.get(k) for k in ("order", "sheet", "row", "market")} | {"host": status["host"]})
    gc("storage", "cp", str(mj), f"gs://{BUCKET}/json/{slug}.json", capture=True)
    # a READY rebuild must not leave the old DRAFT files behind in the bucket
    if status["result"] == "ready":
        for ext in (".docx", ".pdf"):
            gc("storage", "rm", f"gs://{BUCKET}/output/{slug}/{slug}-cmi-whitepaper.DRAFT{ext}", check=False, capture=True)
    gc("storage", "rsync", "--recursive", str(mdir), f"gs://{BUCKET}/output/{slug}", capture=True)
    tmp = mdir / "_status_upload.json"
    tmp.write_text(json.dumps(status, indent=2), encoding="utf-8")
    gc("storage", "cp", str(tmp), f"gs://{BUCKET}/status/{slug}.json", capture=True)
    tmp.unlink()
    return status


def _claim(prefix: str, slug: str) -> bool:
    """Exactly one machine gets each market: the claim object can only be created once."""
    tmp = Path(os.environ.get("TEMP", "/tmp")) / f"_claim_{slug}.json"
    tmp.write_text(json.dumps({"host": socket.gethostname(), "t": time.time()}), encoding="utf-8")
    r = _gc("storage", "cp", "--if-generation-match=0", str(tmp), f"gs://{BUCKET}/{prefix}/{slug}.json",
            check=False, capture=True)
    tmp.unlink(missing_ok=True)
    return r.returncode == 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--slugs", default="")
    p.add_argument("--slugs-file", default="", help="one slug per line (the VM rebuild queue)")
    p.add_argument("--refresh-data", action="store_true", help="re-run the CMI agents too (data blockers)")
    p.add_argument("--no-word", action="store_true", help="no MS Word: PDF and page numbers from LibreOffice (VMs)")
    p.add_argument("--workers", type=int, default=1, help="markets rebuilt at the same time")
    p.add_argument("--refresh-prose", action="store_true", help="rewrite the report text (it was incomplete)")
    p.add_argument("--force", action="store_true", help="store the result even if lower than the stored one "
                                                        "(the stored one is known to be wrong, e.g. missing text)")
    p.add_argument("--claim-prefix", default="", help="claim each market under gs://<bucket>/<prefix>/ first, "
                                                       "so several machines share one list")
    a = p.parse_args()
    market_of = {m["slug"]: m for m in (json.loads(x) for x in (HERE / "markets.jsonl").read_text(
        encoding="utf-8").splitlines() if x.strip())}
    if a.slugs_file:
        slugs = [x.strip() for x in Path(a.slugs_file).read_text(encoding="utf-8").splitlines() if x.strip()]
    elif a.slugs:
        slugs = [s.strip() for s in a.slugs.split(",") if s.strip()]
    else:
        slugs = [s["slug"] for s in _ls_json("status") if s.get("result") == "draft"]
    print(f"rebuilding {len(slugs)} market(s) with {a.workers} worker(s)", flush=True)
    counts: dict[str, int] = {}
    lock = threading.Lock()

    def one(slug: str) -> None:
        if a.claim_prefix and not _claim(a.claim_prefix, slug):
            return                                   # another machine has it
        try:
            s = rebuild(slug, a.refresh_data, market_of, a.no_word, a.refresh_prose, a.force)
        except Exception as exc:  # noqa: BLE001 - logged, the next market goes on
            with lock:
                print(f"ERROR  {slug}: {exc}", flush=True)
                counts["error"] = counts.get("error", 0) + 1
            return
        # only this rebuild's lines (run.log is appended to, the VM run's blockers are above the marker)
        this_run = (OUT / slug / "run.log").read_text(encoding="utf-8", errors="replace").rsplit("=== local rebuild", 1)[-1]
        blockers = [ln.strip() for ln in this_run.splitlines() if ln.strip().startswith("BLOCKER")]
        kept = f" -- bucket kept its {s['kept_previous'].upper()} version" if s.get("kept_previous") else ""
        with lock:
            counts[s["result"]] = counts.get(s["result"], 0) + 1
            print(f"{s['result'].upper():6} {slug} ({s['minutes']} min){kept}" + (": " + "; ".join(b[8:140] for b in blockers)
                                                                                   if blockers else ""), flush=True)

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=max(1, a.workers)) as ex:
        list(ex.map(one, slugs))
    print(f"done: {counts}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
