"""Offline checks of PLAN-01 wiring: the source filter runs on both search
paths, the fleet is deterministic with jitter off, and fields == fields_raw."""
import json

from AiAgentsFleet.field_groups import FIELD_GROUPS

MARKET = "Agricultural Biostimulants Market"
SNIPPETS = [
    {"text": "Agricultural biostimulants market video", "url": "https://youtube.com/w", "domain": "youtube.com"},
    {"text": "Agricultural biostimulants spam", "url": "https://genera.so/a", "domain": "genera.so"},
    {"text": "Agricultural biostimulants market size USD 4.3 billion in 2025",
     "url": "https://precedenceresearch.com/a", "domain": "precedenceresearch.com"},
    {"text": "Agricultural biostimulants guidance", "url": "https://fao.org/b", "domain": "fao.org"},
    {"text": "Agricultural biostimulants news", "url": "https://reuters.com/c", "domain": "reuters.com"},
]
ANSWER = {"base_year": 2025, "forecast_year": 2033, "base_year_value_usd_mn": 4300.0, "cagr_pct": 11.3,
          "historical_usd_mn": {"2023": 3500.0, "2024": 3880.0}}


class StubLLM:
    def __init__(self):
        self.prompts = []

    def complete_json(self, system, user):
        self.prompts.append(user)
        return json.loads(json.dumps(ANSWER))


def test_field_agent_filters_sources(monkeypatch, randomizer_off):
    from AiAgentsFleet import field_agent
    monkeypatch.setattr(field_agent, "gather_with_sources", lambda *a, **k: list(SNIPPETS))
    llm, log = StubLLM(), []
    group = next(g for g in FIELD_GROUPS if g["key"] == "market_size")
    out = field_agent.run_field_group(MARKET, group, llm, log)
    assert "youtube" not in llm.prompts[0] and "genera.so" not in llm.prompts[0]
    src = out.pop("_sources")
    assert out == ANSWER                                     # no jitter
    assert src["num_sources"] == 3
    assert src["dropped_domains"] == ["genera.so", "youtube.com"]
    assert src["quality"]["trusted"] == 3
    assert "low_evidence" not in src
    assert any("no jitter" in line for line in log)
    assert not any("randomized" in line for line in log)


def test_run_data_search_filters_before_topic_filter(monkeypatch):
    from AiAgentsFleet.cmi_report import run_data
    monkeypatch.setattr(run_data, "gather_with_sources", lambda *a, **k: list(SNIPPETS))
    monkeypatch.setattr(run_data.time, "sleep", lambda s: None)
    log, dropped = [], []
    kept = run_data.search(MARKET, ["q"], "countries_mea", log, min_hits=2, dropped_out=dropped)
    assert {s["domain"] for s in kept} == {"precedenceresearch.com", "fao.org", "reuters.com"}
    assert {d["domain"] for d in dropped} == {"youtube.com", "genera.so"}


def test_run_data_group_keeps_numbers_and_flags_low_evidence(monkeypatch, randomizer_off):
    from AiAgentsFleet.cmi_report import run_data
    from AiAgentsFleet.cmi_report.groups import GROUPS
    thin = [SNIPPETS[0], {"text": "Agricultural biostimulants blog", "url": "https://blog.example.com",
                          "domain": "blog.example.com"}]
    monkeypatch.setattr(run_data, "gather_with_sources", lambda *a, **k: list(thin))
    monkeypatch.setattr(run_data.time, "sleep", lambda s: None)
    g = next(g for g in GROUPS if g["key"] == "countries_mea")
    assert g["randomize"]                                    # groups.py still asks for jitter ...
    out = run_data.run_group(MARKET, g, {}, {}, StubLLM(), [])
    src = out.pop("_sources")
    assert out == ANSWER                                     # ... but the switch is off
    assert src["low_evidence"] is True
    assert src["dropped_domains"] == ["youtube.com"]


def _stub_fleet(monkeypatch):
    from AiAgentsFleet import field_agent, run_fleet

    class Cfg:
        is_online = True
    monkeypatch.setattr(run_fleet.GenConfig, "load", staticmethod(lambda root: Cfg()))
    monkeypatch.setattr(run_fleet, "GenLLM", lambda cfg, cache_path=None: StubLLM())
    monkeypatch.setattr(run_fleet, "generate_market_definition", lambda m, llm, cfg, log: {"included": "x"})
    monkeypatch.setattr(field_agent, "gather_with_sources", lambda *a, **k: list(SNIPPETS))
    return run_fleet


def test_run_fleet_deterministic_and_raw_equals_fields(monkeypatch, randomizer_off):
    run_fleet = _stub_fleet(monkeypatch)
    a = run_fleet.run_fleet(MARKET, FIELD_GROUPS, workers=4)
    b = run_fleet.run_fleet(MARKET, FIELD_GROUPS, workers=4)
    dump = lambda d: json.dumps(d, sort_keys=False, ensure_ascii=False)  # noqa: E731
    assert dump(a["fields"]) == dump(b["fields"])            # byte-identical, order included
    assert a["fields"] == a["fields_raw"]
    assert list(a["fields"]) == [g["key"] for g in FIELD_GROUPS]
    assert a["randomization"] == "none"


def test_run_fleet_keeps_raw_when_jitter_on(monkeypatch, randomizer_off):
    run_fleet = _stub_fleet(monkeypatch)
    monkeypatch.setattr(randomizer_off, "ENABLED", True)
    a = run_fleet.run_fleet(MARKET, FIELD_GROUPS[:1], workers=1)
    assert a["fields_raw"]["market_size"]["base_year_value_usd_mn"] == 4300.0
    assert a["fields"]["market_size"]["base_year_value_usd_mn"] != 4300.0
    assert a["fields"]["market_size"]["_sources"] == a["fields_raw"]["market_size"]["_sources"]
    assert a["randomization"] != "none"


def test_field_agent_drops_off_topic_and_retries_once(monkeypatch, randomizer_off):
    from AiAgentsFleet import field_agent
    off_topic = [{"text": "Regional: adjective, of a region", "url": "https://dictionary.cambridge.org/r",
                  "domain": "dictionary.cambridge.org"},
                 {"text": "Wordle answer today", "url": "https://globle.wordleday.org/", "domain": "globle.wordleday.org"}]
    calls, sleeps = [], []
    monkeypatch.setattr(field_agent, "gather_with_sources",
                        lambda *a, **k: calls.append(1) or off_topic + SNIPPETS[2:3])
    monkeypatch.setattr(field_agent.time, "sleep", sleeps.append)
    llm, log = StubLLM(), []
    group = next(g for g in FIELD_GROUPS if g["key"] == "drivers_restraints")   # not a numeric group
    out = field_agent.run_field_group(MARKET, group, llm, log)
    assert len(calls) == 2 and len(sleeps) == 1                # thin -> one retry, no more
    assert "dictionary" not in llm.prompts[0] and "Wordle" not in llm.prompts[0]
    assert out["_sources"]["num_sources"] == 1
    assert out["_sources"]["off_topic_dropped"] == 2
    assert out["_sources"]["low_evidence"] is True             # still thin after the retry


def test_field_agent_no_retry_when_enough_on_topic(monkeypatch, randomizer_off):
    from AiAgentsFleet import field_agent
    calls = []
    monkeypatch.setattr(field_agent, "gather_with_sources", lambda *a, **k: calls.append(1) or list(SNIPPETS))
    monkeypatch.setattr(field_agent.time, "sleep", lambda s: None)
    group = next(g for g in FIELD_GROUPS if g["key"] == "market_size")
    out = field_agent.run_field_group(MARKET, group, StubLLM(), [])
    assert len(calls) == 1
    assert out["_sources"]["off_topic_dropped"] == 0
