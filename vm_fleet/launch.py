"""Local control script for the whitepaper VM fleet on GCP (run from the repo root).

    python vm_fleet/launch.py prepare                       # market list + code + .env -> the bucket
    python vm_fleet/launch.py create --vms 1 --workers 1 --only aquaculture-production-market   # one-market test
    python vm_fleet/launch.py create --vms 4 --workers 6    # the full queue: 4 VMs x 6 markets at a time
    python vm_fleet/launch.py status                        # ready / draft / failed / running counts + VMs
    python vm_fleet/launch.py logs wp-worker-1              # the VM's worker log (serial console)
    python vm_fleet/launch.py download                      # gs://<bucket>/output -> AiAgentsFleet/output
    python vm_fleet/launch.py download-json                 # every market's one-file JSON -> vm_fleet/json/
    python vm_fleet/launch.py reset-failed                  # release failed markets so the next run retries them
    python vm_fleet/launch.py delete                        # delete the worker VMs

VMs power themselves off when the queue is empty (pass --keep to leave them up).
Re-run `prepare` after changing code; a VM picks up the new code on its next boot.
Quota on project tinysql: 16 vCPUs across all regions, so at most 4 x e2-standard-4.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PROJECT = "tinysql"
ZONE = "us-central1-a"
BUCKET = "tinysql-whitepapers"
PREFIX = "wp-worker"
# what a VM needs to run the CMI pipeline (no frontend, backend, outputs or git history)
CODE = ["AiAgentsFleet", "src/me_engine", "templates", "vm_fleet", "vitamin_c_market_whitepaper_v2.docx"]
SKIP_DIRS = {"output", "node_modules", "__pycache__", ".pytest_cache"}


def gcloud_bin() -> str:
    found = shutil.which("gcloud")
    if found:
        return found
    win = Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Cloud SDK/google-cloud-sdk/bin/gcloud.cmd"
    if win.exists():
        return str(win)
    raise SystemExit("gcloud not found -- install the Google Cloud CLI")


def gc(*args: str, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    cmd = [gcloud_bin(), *args, f"--project={PROJECT}"]
    r = subprocess.run(cmd, text=True, capture_output=capture)
    if check and r.returncode != 0:
        raise SystemExit(f"failed: {' '.join(args[:4])}\n{(r.stderr or '')[-2000:]}")
    return r


def tar_code() -> Path:
    def keep(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        parts = Path(info.name).parts
        if (any(p in SKIP_DIRS for p in parts) or info.name.endswith((".pyc", ".env"))
                or info.name.startswith("vm_fleet/json")):            # downloaded results, not code
            return None
        return info
    out = Path(tempfile.gettempdir()) / "wp_code.tgz"
    with tarfile.open(out, "w:gz") as tf:
        for item in CODE:
            tf.add(ROOT / item, arcname=item, filter=keep)
    return out


def cmd_prepare(a) -> None:
    subprocess.run([sys.executable, str(HERE / "prepare_markets.py"), *(["--also", a.also] if a.also else []),
                    *(["--then-priority1"] if a.then_priority1 else [])], check=True)
    exists = gc("storage", "buckets", "describe", f"gs://{BUCKET}", check=False, capture=True).returncode == 0
    if not exists:
        gc("storage", "buckets", "create", f"gs://{BUCKET}", f"--location={ZONE.rsplit('-', 1)[0]}",
           "--uniform-bucket-level-access", "--public-access-prevention")
        print(f"created gs://{BUCKET}")
    # the VMs run as the default compute service account: give it object access on this bucket only
    sa = gc("compute", "project-info", "describe", "--format=value(defaultServiceAccount)", capture=True).stdout.strip()
    gc("storage", "buckets", "add-iam-policy-binding", f"gs://{BUCKET}", f"--member=serviceAccount:{sa}",
       "--role=roles/storage.objectAdmin", capture=True)
    env = ROOT / ".env"
    if not env.exists():
        raise SystemExit(f"{env} missing")
    code = tar_code()
    print(f"code bundle {code.stat().st_size / 1e6:.1f} MB")
    gc("storage", "cp", str(code), f"gs://{BUCKET}/code/code.tgz")
    gc("storage", "cp", str(env), f"gs://{BUCKET}/secrets/.env")
    gc("storage", "cp", str(HERE / "markets.jsonl"), f"gs://{BUCKET}/markets.jsonl")
    print(f"uploaded code, .env and markets.jsonl to gs://{BUCKET}")


def cmd_create(a) -> None:
    startup = Path(tempfile.gettempdir()) / "wp_startup.sh"
    startup.write_bytes((HERE / "startup.sh").read_bytes().replace(b"\r\n", b"\n"))   # LF for bash
    meta = f"bucket={BUCKET},workers={a.workers},auto_shutdown={'0' if a.keep else '1'},mode={a.mode}"
    if a.ds_floor:
        meta += f",ds_floor={a.ds_floor}"
    if a.only:
        meta += f",only={a.only.replace(',', ';')}"
    names = [f"{PREFIX}-{i}" for i in range(a.start, a.start + a.vms)]
    args = ["compute", "instances", "create", *names, f"--zone={ZONE}", f"--machine-type={a.machine}",
            "--image-family=ubuntu-2404-lts-amd64", "--image-project=ubuntu-os-cloud",
            "--boot-disk-size=30GB", "--scopes=cloud-platform", f"--metadata={meta}",
            f"--metadata-from-file=startup-script={startup}", "--labels=app=whitepaper"]
    if a.spot:
        args += ["--provisioning-model=SPOT", "--instance-termination-action=STOP"]
    gc(*args)
    print(f"created {', '.join(names)} -- first boot installs packages (~3-5 min) before markets start")


def _ls_json(prefix: str, tries: int = 3) -> list[dict]:
    """Every gs://<bucket>/<prefix>/*.json, parsed. Mirrored into a local folder with `rsync` and read from
    disk: a single `storage cat` of hundreds of objects came back truncated on a flaky connection
    (2026-10-22: 135 of 331 statuses), and folders were rebuilt from the partial list. The listing is
    compared with the files read; a short read is retried, and raises if it stays short."""
    mirror = Path(tempfile.gettempdir()) / f"wp_mirror_{prefix.replace('/', '_')}"
    mirror.mkdir(parents=True, exist_ok=True)
    for k in range(tries):
        listed = gc("storage", "ls", f"gs://{BUCKET}/{prefix}/", check=False, capture=True).stdout
        names = {l.rsplit("/", 1)[-1] for l in listed.split() if l.endswith(".json")}
        gc("storage", "rsync", f"gs://{BUCKET}/{prefix}", str(mirror), "--delete-unmatched-destination-objects",
           check=False, capture=True)
        out, bad = [], []
        for n in names:
            try:
                out.append(json.loads((mirror / n).read_text(encoding="utf-8")))
            except (OSError, ValueError):
                bad.append(n)
        if names and not bad:
            return out
        time.sleep(5 * (k + 1))
    raise RuntimeError(f"could not read gs://{BUCKET}/{prefix}: {len(bad)} of {len(names)} objects missing locally")


def cmd_status(a) -> None:
    gc("compute", "instances", "list", f"--filter=labels.app=whitepaper",
       "--format=table(name,zone.basename(),status,machineType.basename())")
    status = {s["slug"]: s for s in _ls_json("status")}
    claims = gc("storage", "ls", f"gs://{BUCKET}/claims/", check=False, capture=True).stdout.split()
    claimed = {Path(c).stem for c in claims if c.endswith(".json")}
    counts: dict[str, int] = {}
    for s in status.values():
        counts[s["result"]] = counts.get(s["result"], 0) + 1
    running = sorted(claimed - set(status))
    total = sum(1 for _ in (HERE / "markets.jsonl").open(encoding="utf-8")) if (HERE / "markets.jsonl").exists() else "?"
    print(f"\nmarkets: {total} | done: {len(status)} {counts} | running: {len(running)}")
    for s in running[:20]:
        print(f"  running  {s}")
    for s in sorted(status.values(), key=lambda x: x.get("finished", "")):
        if a.all or s["result"] != "ready":
            print(f"  {s['result']:6} {s['slug']} ({s.get('minutes', '?')} min, {s.get('host', '?')})")


def cmd_logs(a) -> None:
    r = gc("compute", "instances", "get-serial-port-output", a.vm, f"--zone={ZONE}", capture=True)
    lines = [ln for ln in r.stdout.splitlines() if "startup-script" in ln or "[wp-" in ln or "===" in ln]
    print("\n".join(lines[-a.n:]))


def cmd_download(a) -> None:
    dest = Path(a.dest) if a.dest else ROOT / "AiAgentsFleet" / "output"
    dest.mkdir(parents=True, exist_ok=True)
    gc("storage", "rsync", "--recursive", f"gs://{BUCKET}/output", str(dest))
    print(f"downloaded to {dest}")


def cmd_download_json(a) -> None:
    """Every market's one-file JSON into one folder (default vm_fleet/json/)."""
    dest = Path(a.dest) if a.dest else HERE / "json"
    dest.mkdir(parents=True, exist_ok=True)
    gc("storage", "rsync", f"gs://{BUCKET}/json", str(dest))
    print(f"{len(list(dest.glob('*.json')))} market JSON file(s) in {dest}")


def cmd_reset_failed(a) -> None:
    failed = ([s.strip() for s in a.slugs.split(",") if s.strip()] if a.slugs else
              [s["slug"] for s in _ls_json("status") if s["result"] == "failed" or (a.drafts and s["result"] == "draft")])
    for slug in failed:
        # one call per object: `rm a b` fails as a whole when b is missing (a run that never wrote a status),
        # and the claim then stays, so no worker picks the market up again
        for obj in ("claims", "status"):
            gc("storage", "rm", f"gs://{BUCKET}/{obj}/{slug}.json", check=False, capture=True)
    print(f"released {len(failed)} market(s): {', '.join(failed) or '-'}")


def cmd_hotpatch(a) -> None:
    """Uploads the current code and unpacks it on every running worker VM, without a restart: each market
    runs as a new process, so every market started after this uses the new code (running ones finish
    on the old)."""
    code = tar_code()
    gc("storage", "cp", str(code), f"gs://{BUCKET}/code/code.tgz", capture=True)
    r = gc("compute", "instances", "list", "--filter=labels.app=whitepaper AND status=RUNNING",
           "--format=value(name)", capture=True)
    for name in r.stdout.split():
        res = gc("compute", "ssh", name, f"--zone={ZONE}", "--quiet", "--command",
                 f"sudo gcloud storage cp gs://{BUCKET}/code/code.tgz /tmp/code.tgz --quiet && "
                 f"sudo tar xzf /tmp/code.tgz -C /opt/wp/code && echo patched", check=False, capture=True)
        print(f"{name}: {'patched' if 'patched' in res.stdout else 'FAILED ' + (res.stderr or '')[-300:]}")


def cmd_delete(a) -> None:
    r = gc("compute", "instances", "list", "--filter=labels.app=whitepaper", "--format=value(name)", capture=True)
    names = r.stdout.split()
    if names:
        gc("compute", "instances", "delete", *names, f"--zone={ZONE}", "--quiet")
    print(f"deleted {', '.join(names) or 'nothing'}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    pp = sub.add_parser("prepare")
    pp.add_argument("--also", default="", help="slugs from markets_priority1.csv to add to the queue (first)")
    pp.add_argument("--then-priority1", action="store_true", help="queue all Priority 1 markets after Priority 2")
    c = sub.add_parser("create")
    c.add_argument("--vms", type=int, default=1)
    c.add_argument("--start", type=int, default=1, help="number of the first VM (wp-worker-<start>)")
    c.add_argument("--workers", type=int, default=6, help="markets built at once per VM")
    c.add_argument("--machine", default="e2-standard-4")
    c.add_argument("--only", default="", help="comma-separated slugs to run (default: the whole list)")
    c.add_argument("--keep", action="store_true", help="don't power off when the queue is empty")
    c.add_argument("--spot", action="store_true", help="Spot VMs (cheaper, can be preempted; needs Spot quota)")
    c.add_argument("--ds-floor", default="", help="DeepSeek spend cap: stop when the balance falls below this ($)")
    c.add_argument("--mode", default="build", choices=["build", "rebuild"],
                   help="build: run the market queue; rebuild: rebuild gs://<bucket>/rebuild_queue.txt from saved data")
    s = sub.add_parser("status")
    s.add_argument("--all", action="store_true", help="list ready markets too")
    lg = sub.add_parser("logs")
    lg.add_argument("vm")
    lg.add_argument("-n", type=int, default=60)
    d = sub.add_parser("download")
    d.add_argument("--dest")
    dj = sub.add_parser("download-json")
    dj.add_argument("--dest")
    r = sub.add_parser("reset-failed")
    r.add_argument("--drafts", action="store_true", help="also release DRAFT markets")
    r.add_argument("--slugs", default="", help="release exactly these markets (comma-separated), whatever their result")
    sub.add_parser("delete")
    sub.add_parser("hotpatch")
    a = p.parse_args()
    {"prepare": cmd_prepare, "create": cmd_create, "status": cmd_status, "logs": cmd_logs,
     "download": cmd_download, "download-json": cmd_download_json, "reset-failed": cmd_reset_failed,
     "delete": cmd_delete, "hotpatch": cmd_hotpatch}[a.cmd](a)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
