"""PLAN-02: the market_size headline must be backed by a quote in its own
evidence; otherwise it is re-asked once and then labelled a CMI estimate."""
import copy
import json

from AiAgentsFleet import consistency
from AiAgentsFleet.cmi_report.guard import drop_size_comparison
from AiAgentsFleet.cmi_report.verify_size import amounts_usd_mn, check_with_reask, verify_market_size

Q25 = "The global agricultural biostimulants market size was valued at USD 4.30 billion in 2025"
Q24 = "The agricultural biostimulants market was worth USD 4,000 million in 2024 and is expected to grow at a CAGR of 7.5% to 2034"
SNIPPETS = [
    {"text": f"{Q25} and is projected to reach USD 10.9 billion by 2034.", "url": "https://www.precedenceresearch.com/x",
     "domain": "precedenceresearch.com"},
    {"text": f"Report summary. {Q24}.", "url": "https://www.grandviewresearch.com/y", "domain": "grandviewresearch.com"},
]


def _ms(value, basis, cagr=11.3, year=2025):
    return {"base_year": year, "base_year_value_usd_mn": value, "cagr_pct": cagr, "forecast_year": 2033,
            "basis": basis, "how_derived": "Taken from the quote."}


def test_quote_present_with_matching_number_is_sourced():
    ms = _ms(4300.0, [{"quote": Q25, "publisher": "Precedence Research", "value_usd_mn": 4300, "year": 2025,
                       "cagr_pct": None}])
    chk = verify_market_size(ms, SNIPPETS)
    assert chk["status"] == "sourced", chk["notes"]
    assert chk["source"]["domain"] == "precedenceresearch.com"
    assert chk["source"]["publisher"] == "Precedence Research"
    assert chk["source"]["derivation"] == "as quoted"
    assert chk["cagr_sourced"] is False                    # 11.3% is not in any quote


def test_quote_not_in_snippets_is_unsourced():
    ms = _ms(4300.0, [{"quote": "The biostimulants market reached USD 4.30 billion in 2025 globally",
                       "value_usd_mn": 4300, "year": 2025}])
    chk = verify_market_size(ms, SNIPPETS)
    assert chk["status"] == "unsourced"
    assert "not found in the evidence" in chk["notes"][0]


def test_number_not_in_quote_is_unsourced():
    ms = _ms(4600.0, [{"quote": Q25, "value_usd_mn": 4600, "year": 2025}])
    chk = verify_market_size(ms, SNIPPETS)
    assert chk["status"] == "unsourced"
    assert "not stated in its quote" in chk["notes"][0]


def test_headline_far_from_quoted_number_is_unsourced():
    ms = _ms(4600.0, [{"quote": Q25, "value_usd_mn": 4300, "year": 2025}])   # +7%, beyond +/-2%
    assert verify_market_size(ms, SNIPPETS)["status"] == "unsourced"


def test_2024_value_normalised_to_2025_is_sourced():
    ms = _ms(4300.0, [{"quote": Q24, "publisher": "Grand View Research", "value_usd_mn": 4000, "year": 2024,
                       "cagr_pct": 7.5}], cagr=7.5)
    chk = verify_market_size(ms, SNIPPETS)
    assert chk["status"] == "sourced", chk["notes"]
    assert chk["source"]["derivation"] == "2024 value carried to 2025 at the quoted 7.5% CAGR"
    assert chk["cagr_sourced"] is True


def test_2024_value_without_quoted_cagr_is_unsourced():
    q = "The agricultural biostimulants market was worth USD 4,000 million in 2024"
    ms = _ms(4300.0, [{"quote": q, "value_usd_mn": 4000, "year": 2024, "cagr_pct": None}])
    chk = verify_market_size(ms, SNIPPETS)
    assert chk["status"] == "unsourced"
    assert "no quoted CAGR" in " ".join(chk["notes"])


def test_no_basis_is_unsourced():
    assert verify_market_size(_ms(4300.0, []), SNIPPETS)["status"] == "unsourced"


def test_amounts_parse_units_and_skip_other_currencies():
    assert amounts_usd_mn("USD 4.3 billion in 2025, up from $3,950 million") == [4300.0, 3950.0]
    assert amounts_usd_mn("EUR 4.3 billion in 2025") == []


def test_reask_once_then_keeps_sourced_answer():
    bad = _ms(4600.0, [{"quote": "made up quote about biostimulants worth USD 4.6 billion in 2025",
                        "value_usd_mn": 4600, "year": 2025}])
    good = _ms(4300.0, [{"quote": Q25, "publisher": "Precedence Research", "value_usd_mn": 4300, "year": 2025}])
    notes, log = [], []
    ms, chk = check_with_reask(bad, SNIPPETS, lambda note: notes.append(note) or copy.deepcopy(good), log)
    assert len(notes) == 1 and "made up quote" in notes[0]       # the agent is told which quote failed
    assert ms["base_year_value_usd_mn"] == 4300.0
    assert chk["status"] == "sourced" and chk["reasked"] is True
    assert chk["first_attempt"]["status"] == "unsourced"


def test_reask_only_once_and_stays_unsourced():
    calls, log = [], []
    bad = _ms(4600.0, [])
    ms, chk = check_with_reask(bad, SNIPPETS, lambda note: calls.append(1) or copy.deepcopy(bad), log)
    assert len(calls) == 1
    assert chk["status"] == "unsourced"
    assert any("CMI estimate" in line for line in log)


def test_no_reask_when_sourced():
    good = _ms(4300.0, [{"quote": Q25, "value_usd_mn": 4300, "year": 2025}])
    _, chk = check_with_reask(good, SNIPPETS, lambda note: 1 / 0, [])
    assert chk["status"] == "sourced" and chk["reasked"] is False


def test_field_agent_keeps_snippets_and_check_for_market_size(monkeypatch, randomizer_off):
    from AiAgentsFleet import field_agent
    from AiAgentsFleet.field_groups import FIELD_GROUPS
    answer = _ms(4300.0, [{"quote": Q25, "value_usd_mn": 4300, "year": 2025}])

    class LLM:
        calls = 0

        def complete_json(self, system, user):
            LLM.calls += 1
            return copy.deepcopy(answer)
    monkeypatch.setattr(field_agent, "gather_with_sources", lambda *a, **k: [dict(s) for s in SNIPPETS] * 2)
    group = next(g for g in FIELD_GROUPS if g["key"] == "market_size")
    out = field_agent.run_field_group("Agricultural Biostimulants Market", group, LLM(), [])
    assert LLM.calls == 1
    assert out["_sources"]["size_check"]["status"] == "sourced"
    assert out["_sources"]["snippets"][0]["domain"] == "precedenceresearch.com"
    other = next(g for g in FIELD_GROUPS if g["key"] == "regional")
    assert "snippets" not in field_agent.run_field_group("Agricultural Biostimulants Market", other, LLM(), [])["_sources"]


def test_scenarios_keep_ordered_agent_values():
    c = []
    scen = {"bear": {"cagr_pct": 18}, "base": {}, "bull": {"cagr_pct": 45}}
    out = consistency._reconcile_scenarios(scen, 267.5, 1500.0, 24.2, 8, c)
    assert out["bear"]["cagr_pct"] == 18 and out["bull"]["cagr_pct"] == 45     # GenAI raw 18/32/45 kept
    assert not any("clamped" in x for x in c)


def test_scenarios_clamp_only_out_of_order_values():
    c = []
    scen = {"bear": {"cagr_pct": 12}, "base": {}, "bull": {"cagr_pct": 9}}
    out = consistency._reconcile_scenarios(scen, 100.0, 200.0, 10.0, 8, c)
    assert out["bear"]["cagr_pct"] == 8.0                         # 12 >= base: clamped to base - 2
    assert out["bull"]["cagr_pct"] == 12.0                        # 9 <= base: clamped to base + 2
    assert sum("clamped" in x for x in c) == 2


def test_guard_drops_gap_explanations():
    dropped = []
    text = ("CMI estimates the market at $4.60B in 2025. The gap reflects scope differences between publishers. "
            "Published estimates vary because of different definitions. MEA grows faster than global.")
    out = drop_size_comparison(text, dropped)
    assert out == "CMI estimates the market at $4.60B in 2025. MEA grows faster than global."
    assert len(dropped) == 2


def test_guard_drops_published_wording_when_unsourced():
    dropped = []
    text = "Published estimates range widely. CMI triangulates its base. The market is a CMI estimate."
    assert drop_size_comparison(text, dropped, unsourced=True) == "The market is a CMI estimate."
    assert drop_size_comparison("Published estimates range widely.", [], unsourced=False) \
        == "Published estimates range widely."


def test_model_drops_published_figures_when_unsourced():
    from AiAgentsFleet.cmi_report import model
    fleet = {"fields_raw": {"market_size": {"_sources": {"size_check": {"status": "unsourced", "notes": ["x"]}}}}}
    assert model.size_check(fleet)["status"] == "unsourced"
    assert model.size_check({"fields": {}})["status"] == "unsourced"            # pre-PLAN-02 run
    chk = {"status": "sourced", "source": {"publisher": "P", "domain": "p.com"}}
    assert model.size_check({"fields_raw": {"market_size": {"_sources": {"size_check": chk}}}}) == chk
    json.dumps(chk)


# --- CMI's own published figure wins (user decision, PLAN-02 "CMI's own figure") ---
MARKET = "Agricultural Biostimulants Market"
QCMI25 = "The agricultural biostimulants market is estimated to be valued at USD 4.10 Bn in 2025"
QCMI24 = "The agricultural biostimulants market is estimated to be valued at USD 4.0 billion in 2024, growing at a CAGR of 7.5%"
QCMI_PARENT = "The global biostimulants market is estimated to be valued at USD 4.10 Bn in 2025"
CMI_SNIPPETS = SNIPPETS + [
    {"text": f"{QCMI25} and is expected to reach USD 8.9 Bn by 2032.", "domain": "www.coherentmarketinsights.com",
     "url": "https://www.coherentmarketinsights.com/industry-reports/agricultural-biostimulants-market"},
    {"text": f"Press release. {QCMI24}.", "domain": "press.coherentmarketinsights.com",
     "url": "https://press.coherentmarketinsights.com/a"},
    {"text": f"{QCMI_PARENT}.", "domain": "coherentmarketinsights.com",
     "url": "https://www.coherentmarketinsights.com/industry-reports/biostimulants-market"},
]
B_P25 = {"quote": Q25, "publisher": "Precedence Research", "value_usd_mn": 4300, "year": 2025}


def test_cmi_quote_beats_other_verified_quote():
    # the agent names the wrong publisher: CMI is recognised by the snippet's website
    b_cmi = {"quote": QCMI25, "publisher": "Some Firm", "value_usd_mn": 4100, "year": 2025}
    chk = verify_market_size(_ms(4100.0, [B_P25, b_cmi]), CMI_SNIPPETS, MARKET)
    assert chk["status"] == "sourced" and chk["source_is_cmi"] is True
    assert chk["source"]["publisher"] == "Coherent Market Insights"
    # a headline taken from the other (verified) quote fails, and says to use the CMI figure
    chk = verify_market_size(_ms(4300.0, [B_P25, b_cmi]), CMI_SNIPPETS, MARKET)
    assert chk["status"] == "unsourced"
    assert any("Coherent Market Insights' own figure" in n for n in chk["notes"])


def test_cmi_2024_value_carried_to_2025():
    b = {"quote": QCMI24, "value_usd_mn": 4000, "year": 2024, "cagr_pct": 7.5}
    chk = verify_market_size(_ms(4300.0, [B_P25, b], cagr=7.5), CMI_SNIPPETS, MARKET)
    assert chk["status"] == "sourced" and chk["source_is_cmi"] is True
    assert chk["source"]["derivation"] == "2024 value carried to 2025 at the quoted 7.5% CAGR"


def test_cmi_quote_for_parent_market_not_used():
    b = {"quote": QCMI_PARENT, "value_usd_mn": 4100, "year": 2025}
    chk = verify_market_size(_ms(4300.0, [B_P25, b]), CMI_SNIPPETS, MARKET)
    assert chk["status"] == "sourced" and chk["source_is_cmi"] is False     # the other quote is used
    assert chk["source"]["domain"] == "precedenceresearch.com"
    assert any("not for this market" in n for n in chk["notes"])
    # nor can the parent-market CMI figure be the headline itself
    assert verify_market_size(_ms(4100.0, [b]), CMI_SNIPPETS, MARKET)["status"] == "unsourced"


def test_without_cmi_quote_behaviour_unchanged():
    ms = _ms(4300.0, [B_P25])
    with_market = verify_market_size(ms, CMI_SNIPPETS, MARKET)
    assert with_market == verify_market_size(ms, SNIPPETS)
    assert with_market["status"] == "sourced" and with_market["source_is_cmi"] is False


