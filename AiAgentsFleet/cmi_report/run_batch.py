"""Batch: one CMI whitepaper per market name in a text file (one per line).

    python AiAgentsFleet/cmi_report/run_batch.py --markets markets.txt [--redo]
    python AiAgentsFleet/cmi_report/run_batch.py --tracker --limit 10   # next 10: Priority 1 first, then Priority 2

Numbers are never jittered: the batch stops if AIFLEET_RANDOMIZE is set,
unless --allow-randomize is passed (it is then passed on to each market).

Markets are run one at a time (the web search throttles parallel use). A
market whose .docx (or .DRAFT.docx) already exists is skipped unless --redo.
A failure is logged and the batch moves on; batch_log.jsonl records every
outcome, each market's QA counts and whether it passed the release gate
("ready"; a report that did not is saved as .DRAFT.docx).
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
FLEET = HERE.parent
ROOT = FLEET.parent
sys.path.insert(0, str(ROOT))

from AiAgentsFleet import randomizer  # noqa: E402
from AiAgentsFleet.cmi_report import tracker  # noqa: E402

NOT_READY = 3      # run_cmi_report.py exit code: built, failed the release gate


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


def main() -> int:
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--markets", help="text file, one market name per line")
    g.add_argument("--tracker", nargs="*", default=None,
                   help="run tracker CSV(s), worked through in the order given (default: AllMarkets/"
                        "markets_priority1.csv, then markets_priority2.csv); each market is labelled pending / "
                        "generated / draft / failed and 'generated' is never run again")
    p.add_argument("--limit", type=int, default=None, help="with --tracker: run only the next N markets (a batch)")
    p.add_argument("--sync", action="store_true",
                   help="with --tracker: only label the markets whose report already exists, run nothing")
    p.add_argument("--redo", action="store_true", help="also re-run drafts (generated reports are never re-run "
                                                        "from the tracker)")
    p.add_argument("--allow-randomize", action="store_true",
                   help="permit AIFLEET_RANDOMIZE=1 (jittered numbers; never for client reports)")
    a = p.parse_args()
    randomizer.require_off(a.allow_randomize)
    use_tracker = a.tracker is not None
    sheets: dict[Path, list[dict]] = {}       # tracker CSV -> its rows, in run order
    by_name: dict[str, tuple[dict, Path]] = {}
    if use_tracker:
        todo: list[tuple[dict, Path]] = []
        for path in [Path(t) for t in a.tracker] or tracker.TRACKERS:
            rows = sheets[path] = tracker.load(path)
            changed = tracker.sync(rows)
            tracker.save(rows, path)
            counts = {st: sum(1 for r in rows if r["status"] == st) for st in ("generated", "draft", "failed", "pending")}
            print(f"Tracker {path.name}: {counts}" + (f" ({changed} label(s) updated from the output folder)" if changed else ""))
            seen = {r["slug"] for r, _ in todo}
            todo += [(r, path) for r in rows if r["status"] not in tracker.DONE and (a.redo or r["status"] != "draft")
                     and r["slug"] not in seen]
        if a.sync:
            return 0
        todo = todo[:a.limit] if a.limit else todo
        names = [r["market"] for r, _ in todo]
        by_name = {r["market"]: (r, path) for r, path in todo}
    else:
        names = [x.strip() for x in Path(a.markets).read_text(encoding="utf-8").splitlines()
                 if x.strip() and not x.startswith("#")]
    log_path = FLEET / "output" / "batch_log.jsonl"
    for k, name in enumerate(names, start=1):
        s = slug(name)
        docx = FLEET / "output" / s / f"{s}-cmi-whitepaper.docx"
        draft = docx.with_name(docx.stem + ".DRAFT.docx")
        if docx.exists() or (draft.exists() and not a.redo):
            print(f"[{k}/{len(names)}] skip (done{'' if docx.exists() else ', not ready'}): {name}")
            continue
        print(f"[{k}/{len(names)}] {name}")
        t0 = time.time()
        cmd = [sys.executable, str(HERE / "run_cmi_report.py"), "--market", name]
        res = subprocess.run(cmd + (["--allow-randomize"] if a.allow_randomize else []), cwd=ROOT)
        built = res.returncode in (0, NOT_READY)             # NOT_READY: built, but failed the release gate
        qa_path = docx.parent / "qa_report.json"
        qa = json.loads(qa_path.read_text(encoding="utf-8")) if qa_path.exists() and built else {}
        au_path = docx.parent / "audit_report.json"
        gate = (json.loads(au_path.read_text(encoding="utf-8")).get("gate") or {}) if au_path.exists() and built else {}
        entry = {"market": name, "slug": s, "ok": built, "minutes": round((time.time() - t0) / 60, 1),
                 "at": datetime.now(timezone.utc).isoformat(),
                 "ready": res.returncode == 0 and bool(gate.get("ready")),
                 "blockers": len(gate.get("blockers") or []),
                 "qa": {k2: len(qa.get(k2) or []) for k2 in ("numbers_not_in_model", "template_leftovers",
                                                             "drone_wording")}}
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        print(f"   -> {'FAILED' if not built else 'READY' if entry['ready'] else 'NOT READY (DRAFT)'} in "
              f"{entry['minutes']} min, {entry['blockers']} blocker(s), QA {entry['qa']}")
        if use_tracker:                       # the label goes into its tracker as soon as the market finishes
            row, path = by_name[name]
            tracker.mark(row, "generated" if entry["ready"] else "draft" if built else "failed", entry["minutes"])
            if not tracker.save(sheets[path], path):
                print(f"   ({path.name} is open in Excel: label saved to {path.stem}.pending.csv, merged next run)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
