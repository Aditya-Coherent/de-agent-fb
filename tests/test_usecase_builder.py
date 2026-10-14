import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from me_engine.generation import usecase_builder as ub  # noqa: E402


def _spec(rate, est=1000.0, price=50_000.0):
    return {"driver": "manufacturing_value_added_usd", "units_formula": "driver * rate",
            "constants": {"rate": {"value": rate}}, "price_usd_per_unit": {"value": price},
            "world_units_per_year": {"value": est}}


def test_units_and_value():
    s = _spec(1e-9)
    assert math.isclose(ub.evaluate_units(s, 2e12), 2000.0)
    assert math.isclose(ub.evaluate_formula(s, 2e12), 2000.0 * 50_000)


def test_driver_name_alias():
    s = _spec(1e-9)
    s["units_formula"] = "manufacturing_value_added_usd * rate"
    assert math.isclose(ub.evaluate_units(s, 1e12), 1000.0)


def test_calibration_sets_level_keeps_country_split():
    dv = {"A": 3e12, "B": 1e12}
    s = ub.calibrate(_spec(5e-7, est=1000.0), dv, ["A", "B"])  # rate off by ~500x
    units = {g: ub.evaluate_units(s, dv[g]) for g in dv}
    assert math.isclose(sum(units.values()), 1000.0)
    assert math.isclose(units["A"] / units["B"], 3.0)
    assert math.isclose(s["calibration"]["raw_world_units"], 2e6)


def test_calibration_skipped_without_estimate():
    s = _spec(1e-9)
    s.pop("world_units_per_year")
    ub.calibrate(s, {"A": 1e12}, ["A"])
    assert "calibration" not in s


def test_circular_world_units_flagged():
    s = {"world_units_per_year": {"reason": "welding market ~$2B divided by average system price $250k"}}
    assert ub.circular_reason(s)
    s = {"world_units_per_year": {"reason": "20,000 plants run laser welding lines, replaced every 8 years"}}
    assert not ub.circular_reason(s)


def test_world_units_from_buyers():
    s = {"world_units_per_year": {"buyers_worldwide": 20000, "units_per_buyer_per_year": 0.4, "value": 1}}
    assert math.isclose(ub.world_units_estimate(s), 8000.0)


def test_mix_shares_are_value_weighted_by_use_case():
    from me_engine.generation import usecase_mix

    class FakeLLM:
        def complete_json(self, system, user):
            if "Welding" in user:
                return {"By Laser Type": {"top": {"Fiber": 1.0}, "sub": {"Fiber": {"CW": 0.9, "Pulsed": 0.1}}}}
            return {"By Laser Type": {"top": {"Fiber": 0.5, "Gas": 0.5}, "sub": {"Fiber": {"CW": 0.0, "Pulsed": 1.0}}}}

    ub_res = {"dimension": "By Application", "country_value": {"DE": 4.0, "IN": 1.0},
              "use_cases": [{"industry": "Ind", "use_case": "Welding", "values_mn": {"DE": 3.0, "IN": 0.0}},
                            {"industry": "Def", "use_case": "C-UAS", "values_mn": {"DE": 1.0, "IN": 1.0}}]}
    nesting = {"By Application": {"__root__": ["Ind", "Def"]},
               "By Laser Type": {"__root__": ["Fiber", "Gas"], "Fiber": ["CW", "Pulsed"]}}
    mix = usecase_mix.build(FakeLLM(), ub_res, nesting, "M", say=lambda *_: None)
    de, in_ = mix["shares"]["By Laser Type"]["DE"], mix["shares"]["By Laser Type"]["IN"]
    assert math.isclose(de["Fiber"], 3.5 / 4.0) and math.isclose(in_["Fiber"], 0.5)
    assert math.isclose(de["Fiber|CW"], 2.7 / 3.5) and math.isclose(in_["Fiber|CW"], 0.0)
