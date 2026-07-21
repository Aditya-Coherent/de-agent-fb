"""Run the ME pipeline on a generated Input Sheet and report gate status."""
from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class MEResult:
    ok: bool
    gate1_passed: bool
    gate3_passed: bool
    output_path: Path
    output_exists: bool
    returncode: int
    log_tail: str
    ref_mean_error: float | None = None
    ref_within_5pct: float | None = None


def run_me_pipeline(input_path: Path, output_path: Path, reference: Path | None = None,
                    root: Path | None = None, timeout: float = 600.0) -> MEResult:
    """Invoke run_pipeline.py as a subprocess. Returns parsed gate status.

    The pipeline writes the ME workbook even when Gate 3 reports identity
    violations, so a produced file with Gate 1 passing is still useful output.
    """
    root = root or Path(__file__).resolve().parents[3]
    args = [sys.executable, "-u", str(root / "run_pipeline.py"),
            "--input", str(input_path), "--output", str(output_path), "--no-agents"]
    if reference is not None and reference.exists():
        args += ["--reference", str(reference)]

    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace")
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")

    gate1 = bool(re.search(r"Gate 1 passed|GATE 1 .*PASSED|PASSED — all checks clean", out))
    gate3 = bool(re.search(r"Gate 3 (passed|PASSED)|GATE 3 .*PASSED", out)) and "Gate 3 FAILED" not in out
    tail = "\n".join(out.splitlines()[-25:])

    ref_err = ref_within = None
    m = re.search(r"([\d.]+)%\s*mean error,\s*([\d.]+)%\s*within 5%", out)
    if m:
        ref_err, ref_within = float(m.group(1)), float(m.group(2))
    return MEResult(
        ok=proc.returncode == 0,
        gate1_passed=gate1,
        gate3_passed=gate3,
        output_path=output_path,
        output_exists=output_path.exists(),
        returncode=proc.returncode,
        log_tail=tail,
        ref_mean_error=ref_err,
        ref_within_5pct=ref_within,
    )
