"""Batch: one CMI whitepaper per market name in a text file (one per line).

    python AiAgentsFleet/cmi_report/run_batch.py --markets markets.txt [--redo]

Numbers are never jittered: the batch stops if AIFLEET_RANDOMIZE is set,
unless --allow-randomize is passed (it is then passed on to each market).

Markets are run one at a time (the web search throttles parallel use). A
market whose .docx already exists is skipped unless --redo. A failure is
logged and the batch moves on; batch_log.jsonl records every outcome and
each market's QA counts.
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


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--markets", required=True, help="text file, one market name per line")
    p.add_argument("--redo", action="store_true")
    p.add_argument("--allow-randomize", action="store_true",
                   help="permit AIFLEET_RANDOMIZE=1 (jittered numbers; never for client reports)")
    a = p.parse_args()
    randomizer.require_off(a.allow_randomize)
    names = [x.strip() for x in Path(a.markets).read_text(encoding="utf-8").splitlines()
             if x.strip() and not x.startswith("#")]
    log_path = FLEET / "output" / "batch_log.jsonl"
    for k, name in enumerate(names, start=1):
        s = slug(name)
        docx = FLEET / "output" / s / f"{s}-cmi-whitepaper.docx"
        if docx.exists() and not a.redo:
            print(f"[{k}/{len(names)}] skip (done): {name}")
            continue
        print(f"[{k}/{len(names)}] {name}")
        t0 = time.time()
        cmd = [sys.executable, str(HERE / "run_cmi_report.py"), "--market", name]
        res = subprocess.run(cmd + (["--allow-randomize"] if a.allow_randomize else []), cwd=ROOT)
        qa_path = docx.parent / "qa_report.json"
        qa = json.loads(qa_path.read_text(encoding="utf-8")) if qa_path.exists() and res.returncode == 0 else {}
        entry = {"market": name, "slug": s, "ok": res.returncode == 0, "minutes": round((time.time() - t0) / 60, 1),
                 "at": datetime.now(timezone.utc).isoformat(),
                 "qa": {k2: len(qa.get(k2) or []) for k2 in ("numbers_not_in_model", "template_leftovers",
                                                             "drone_wording")}}
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        print(f"   -> {'OK' if entry['ok'] else 'FAILED'} in {entry['minutes']} min, QA {entry['qa']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
