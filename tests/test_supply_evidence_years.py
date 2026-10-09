from me_engine.supply.evidence_estimator import extract_real_figures


def _values(text):
    return [f.value_usd_mn for f in extract_real_figures([text]) if f.value_usd_mn is not None]


def test_2026_market_size_is_ignored():
    assert _values("Olive Oil Market Size is USD 16.03 billion in 2026 and expected to reach "
                   "USD 28.94 billion by 2035, at a CAGR of 4.1%.") == []


def test_2025_and_2024_market_sizes_are_kept():
    assert _values("The market was valued at USD 15.67 billion in 2025 and will reach USD 20 billion by 2033") == [15670.0]
    assert _values("In 2024, the olive oil market was worth $14.2 billion") == [14200.0]


def test_picks_the_2025_value_not_the_2026_one():
    assert _values("valued at USD 18.75 billion in 2025 and projected from USD 19.5 billion in 2026") == [18750.0]


def test_value_without_a_year_is_not_used():
    assert _values("Market worth USD 12 billion") == []
