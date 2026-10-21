"""The run trackers (AllMarkets/markets_priority1.csv, then markets_priority2.csv): generated reports are
labelled and never re-run."""
import csv
import subprocess
import sys

from AiAgentsFleet.cmi_report import run_batch, tracker


def _write(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def test_sync_labels_existing_reports_and_marks(tmp_path, monkeypatch):
    monkeypatch.setattr(tracker, "FLEET", tmp_path)
    done = tmp_path / "output" / "a-market" / "a-market-cmi-whitepaper.docx"
    done.parent.mkdir(parents=True)
    done.write_text("x")
    path = tmp_path / "t.csv"
    _write(path, [{"order": 1, "market": "A Market", "slug": "a-market"},
                  {"order": 2, "market": "B Market", "slug": "b-market"}])
    rows = tracker.load(path)
    assert [r["status"] for r in rows] == ["pending", "pending"]
    assert tracker.sync(rows) == 1 and rows[0]["status"] == "generated" and rows[0]["report"].endswith(".docx")
    tracker.mark(rows[1], "failed", 3.0)
    assert tracker.save(rows, path)
    again = tracker.load(path)
    assert [r["status"] for r in again] == ["generated", "failed"] and again[1]["minutes"] == "3.0"


def test_tracker_open_in_excel_goes_to_pending_file(tmp_path, monkeypatch):
    monkeypatch.setattr(tracker, "FLEET", tmp_path)
    path = tmp_path / "t.csv"
    _write(path, [{"order": 1, "market": "A Market", "slug": "a-market"}])
    rows = tracker.load(path)
    tracker.mark(rows[0], "generated", 5.0)
    real_open = type(path).open

    def locked(self, mode="r", *a, **k):          # Excel holds the CSV open
        if self == path and "w" in mode:
            raise PermissionError
        return real_open(self, mode, *a, **k)
    monkeypatch.setattr(type(path), "open", locked)
    assert not tracker.save(rows, path)
    monkeypatch.setattr(type(path), "open", real_open)
    assert tracker.load(path)[0]["status"] == "generated"          # merged from the .pending.csv


def test_batch_runs_priority1_before_priority2(tmp_path, monkeypatch):
    monkeypatch.setattr(tracker, "FLEET", tmp_path)
    monkeypatch.setattr(run_batch, "FLEET", tmp_path)
    (tmp_path / "output").mkdir()
    p1, p2 = tmp_path / "p1.csv", tmp_path / "p2.csv"
    _write(p1, [{"order": 1, "market": "A Market", "slug": "a-market", "status": "generated"},
                {"order": 2, "market": "B Market", "slug": "b-market", "status": "pending"}])
    _write(p2, [{"order": 1, "market": "B Market", "slug": "b-market", "status": "pending"},     # also in P1
                {"order": 2, "market": "C Market", "slug": "c-market", "status": "pending"},
                {"order": 3, "market": "D Market", "slug": "d-market", "status": "pending"}])
    ran = []

    def fake_run(cmd, cwd=None):
        ran.append(cmd[cmd.index("--market") + 1])
        return subprocess.CompletedProcess(cmd, 1)                  # the run crashed -> "failed"
    monkeypatch.setattr(run_batch.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["run_batch.py", "--tracker", str(p1), str(p2), "--limit", "2"])
    assert run_batch.main() == 0
    assert ran == ["B Market", "C Market"]                           # P1 first, no repeat, then P2
    assert [r["status"] for r in tracker.load(p1)] == ["generated", "failed"]
    assert [r["status"] for r in tracker.load(p2)] == ["pending", "failed", "pending"]
