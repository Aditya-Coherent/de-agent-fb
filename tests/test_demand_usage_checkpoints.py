"""Checkpoint (draft/approved) file I/O -- pure filesystem, no LLM/network.

Run with: pytest tests/test_demand_usage_checkpoints.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from me_engine.demandUseCaseCountry_approach2B import checkpoints


def test_slugify_is_filesystem_safe_and_stable():
    assert checkpoints.slugify("Crude Oil") == "crude-oil"
    assert checkpoints.slugify("  Global Widgets & Gadgets!!  ") == "global-widgets-gadgets"
    assert checkpoints.slugify("") == "market"


def test_write_then_read_json_roundtrips(tmp_path):
    path = tmp_path / "sub" / "draft.json"
    data = {"a": 1, "b": [1, 2, 3], "c": {"nested": True}}
    checkpoints.write_json(path, data)
    assert path.exists()
    assert checkpoints.read_json(path) == data


def test_read_json_missing_file_returns_none(tmp_path):
    assert checkpoints.read_json(tmp_path / "does_not_exist.json") is None


def test_read_json_corrupt_file_returns_none_not_raise(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not valid json", encoding="utf-8")
    assert checkpoints.read_json(path) is None


def test_phase_paths_are_distinct_and_under_the_same_run_dir(tmp_path):
    market = "Test Market"
    setup_d = checkpoints.setup_draft_path(market, root=tmp_path)
    setup_a = checkpoints.setup_approved_path(market, root=tmp_path)
    tax_d = checkpoints.taxonomy_draft_path(market, root=tmp_path)
    tax_a = checkpoints.taxonomy_approved_path(market, root=tmp_path)
    sizing = checkpoints.sizing_output_prefix(market, root=tmp_path)

    paths = {setup_d, setup_a, tax_d, tax_a, sizing}
    assert len(paths) == 5  # all distinct
    assert all(p.parent == setup_d.parent for p in paths)  # same run dir
