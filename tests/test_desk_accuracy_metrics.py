"""compare_desk_vs_actuals.score: % error vs the median and in_published_range."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from compare_desk_vs_actuals import score  # noqa: E402

ACTUAL = {"actual": 15_700.0, "published": [15_700.0, 18_750.0, 13_590.0]}


@pytest.mark.parametrize("estimate,in_range", [
    (14_615.0, True), (13_000.0, True),     # 13,590 x 0.95 = 12,910
    (12_800.0, False), (19_600.0, True),    # 18,750 x 1.05 = 19,688
    (19_800.0, False),
])
def test_in_published_range_with_tolerance(estimate, in_range):
    assert score(estimate, ACTUAL)["in_published_range"] is in_range


def test_pct_error_against_median_is_kept():
    assert score(14_615.0, ACTUAL)["pct_error"] == pytest.approx(-0.0691, abs=1e-4)


def test_missing_estimate():
    assert score(None, ACTUAL) == {"pct_error": None, "in_published_range": None}
