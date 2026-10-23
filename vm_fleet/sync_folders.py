"""Rebuilds the two local hand-off folders from the bucket's status records (the source of truth).

    python vm_fleet/sync_folders.py

  whitepaper_jsons_completed/   every market whose status is READY  (the upload folder) + index.csv
  whitepaper_jsons_drafts/      every DRAFT market + index.csv,
                                and _failed_or_interrupted/ (failed runs, quota-cut partial JSONs)

Each folder is built fresh in a staging folder, every JSON validated (opens, right market, model and text
present), and only then swapped in, so a folder is never half-updated. Run it after any rebuild.
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from launch import BUCKET, PROJECT, _ls_json, gcloud_bin  # noqa: E402

COMPLETED = ROOT / "whitepaper_jsons_completed"
DRAFTS = ROOT / "whitepaper_jsons_drafts"
FIELDS = ["order", "sheet", "market", "slug", "status", "global_2026_usd_mn", "global_2033_usd_mn",
          "cagr_2026_33_pct", "headline", "json_file"]


def fetch(slugs: list[str], dest: Path, prefix: str = "json") -> None:
    dest.mkdir(parents=True, exist_ok=True)
    if slugs:
        subprocess.run([gcloud_bin(), "storage", "cp", "-I", str(dest) + "/", f"--project={PROJECT}"],
                       input="\n".join(f"gs://{BUCKET}/{prefix}/{s}.json" for s in slugs) + "\n",
                       text=True, capture_output=True)


def rows_for(folder: Path, status: dict, markets: dict) -> tuple[list[dict], list[str]]:
    rows, bad = [], []
    for f in sorted(folder.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            m = d["model"]
            assert d["meta"]["slug"] == f.stem and m.get("global") and m.get("regions") and len(d.get("text") or {}) >= 20
        except Exception:  # noqa: BLE001
            bad.append(f.stem)
            continue
        g, mk = m["global"], markets.get(f.stem, {})
        rows.append({"order": mk.get("order"), "sheet": mk.get("sheet"), "market": mk.get("market") or d["meta"].get("market"),
                     "slug": f.stem, "status": status.get(f.stem), "global_2026_usd_mn": round(g["v26"], 1),
                     "global_2033_usd_mn": round(g["v33"], 1), "cagr_2026_33_pct": round(g["cagr"], 2),
                     "headline": (m.get("size_check") or {}).get("status"), "json_file": f.name})
    rows.sort(key=lambda r: r["order"] if isinstance(r["order"], int) else 9999)
    return rows, bad


def write_index(folder: Path, rows: list[dict]) -> None:
    with (folder / "index.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def swap(staging: Path, target: Path) -> None:
    old = target.with_name(target.name + ".old")
    if old.exists():
        shutil.rmtree(old)
    if target.exists():
        target.rename(old)
    staging.rename(target)
    if old.exists():
        shutil.rmtree(old)


def main() -> int:
    status = {s["slug"]: s["result"] for s in _ls_json("status")}
    markets = {json.loads(x)["slug"]: json.loads(x) for x in (HERE / "markets.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()}
    listed = subprocess.run([gcloud_bin(), "storage", "ls", f"gs://{BUCKET}/json/", f"--project={PROJECT}"],
                            capture_output=True, text=True).stdout
    in_bucket = {l.rsplit("/", 1)[-1][:-5] for l in listed.split() if l.endswith(".json")}
    ready = sorted(s for s, r in status.items() if r == "ready" and s in in_bucket)
    draft = sorted(s for s, r in status.items() if r == "draft" and s in in_bucket)

    stage_c = COMPLETED.with_name(COMPLETED.name + ".staging")
    stage_d = DRAFTS.with_name(DRAFTS.name + ".staging")
    for p in (stage_c, stage_d):
        if p.exists():
            shutil.rmtree(p)
    fetch(ready, stage_c)
    fetch(draft, stage_d)
    rc, bad_c = rows_for(stage_c, status, markets)
    rd, bad_d = rows_for(stage_d, status, markets)
    # never swap in a short folder: every READY / DRAFT market's JSON must have been fetched and valid
    if len(rc) != len(ready) or len(rd) != len(draft):
        raise SystemExit(f"sync aborted, folders left unchanged: fetched {len(rc)}/{len(ready)} READY and "
                         f"{len(rd)}/{len(draft)} DRAFT JSONs (invalid: {bad_c + bad_d})")
    write_index(stage_c, rc)
    write_index(stage_d, rd)
    # failed runs and quota-cut partial JSONs: kept for reference with the drafts
    sub = stage_d / "_failed_or_interrupted"
    failed = sorted(s for s, r in status.items() if r == "failed")
    fetch(failed, sub, "json-superseded")
    old_inc = ROOT / "whitepaper_jsons_incomplete"
    if old_inc.exists():
        for f in old_inc.glob("*.json"):
            if not (sub / f.name).exists():
                sub.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, sub / f.name)
    swap(stage_c, COMPLETED)
    swap(stage_d, DRAFTS)
    if old_inc.exists():
        shutil.rmtree(old_inc)
    size = lambda p: sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1e6  # noqa: E731
    print(f"completed: {len(rc)} READY JSONs, {size(COMPLETED):.1f} MB" + (f"  INVALID: {bad_c}" if bad_c else ""))
    print(f"drafts:    {len(rd)} DRAFT JSONs + {len(list((DRAFTS / '_failed_or_interrupted').glob('*.json')))} failed/interrupted"
          + (f"  INVALID: {bad_d}" if bad_d else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
