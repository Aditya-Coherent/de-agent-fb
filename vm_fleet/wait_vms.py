"""Blocks until every whitepaper VM is stopped, confirmed by a successful listing that names all of them.
A failed or empty `gcloud` call is never taken as 'stopped' (2026-10-22: a network blip read as 'all done').

    python vm_fleet/wait_vms.py [--expect 4] [--poll 60]
"""
import argparse, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from launch import PROJECT, gcloud_bin  # noqa: E402

p = argparse.ArgumentParser(); p.add_argument("--expect", type=int, default=4); p.add_argument("--poll", type=int, default=60)
a = p.parse_args()
while True:
    r = subprocess.run([gcloud_bin(), "compute", "instances", "list", "--filter=labels.app=whitepaper",
                        "--format=value(name,status)", f"--project={PROJECT}"], capture_output=True, text=True)
    rows = [l.split() for l in r.stdout.splitlines() if l.strip()]
    if r.returncode == 0 and len(rows) >= a.expect and all(s == "TERMINATED" for _, s in rows):
        print("all VMs stopped:", ", ".join(n for n, _ in rows), flush=True)
        break
    time.sleep(a.poll)
