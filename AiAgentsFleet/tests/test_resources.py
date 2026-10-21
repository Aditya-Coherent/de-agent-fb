"""The Resources chapter (changes.txt 2026-10-19): market-research / consulting firms never listed,
unverifiable sources dropped, the proprietary elements always last."""
from AiAgentsFleet.cmi_report import resources


def fake_search(query: str) -> list[dict]:
    known = {"FAOSTAT": "FAOSTAT: Food and agriculture data", "Journal of Applied Phycology":
             "Journal of Applied Phycology | Springer"}
    name = query.strip('"')
    return [{"title": known[name], "text": known[name], "domain": "example.org"}] if name in known else \
        [{"title": "Unrelated page", "text": "nothing relevant here", "domain": "x.com"}]


def test_blocked_unverified_and_proprietary():
    raw = {"primary_interviews": ["Agronomists at cooperatives"],
           "databases": [{"name": "FAOSTAT", "domain": "fao.org"},
                         {"name": "Grand View Research database", "domain": ""},
                         {"name": "Global Biostimulant Ledger", "domain": ""}],
           "journals": [{"name": "Journal of Applied Phycology", "domain": ""},
                        {"name": "MarketsandMarkets insights", "domain": ""}]}
    log: list[str] = []
    out = resources.build(raw, fake_search, log)
    assert out[0] == ("Primary Research interviews", ["Agronomists at cooperatives"])
    assert ("Databases", ["FAOSTAT"]) in out                 # invented ledger and Grand View dropped
    assert ("Journals", ["Journal of Applied Phycology"]) in out
    assert out[-1][0] == "Proprietary Elements" and "CMI Data Analytics Tool" in out[-1][1][0]
    assert any("Grand View" in x for x in log) and any("Ledger" in x for x in log)


def test_search_down_keeps_with_note():
    log: list[str] = []
    out = resources.build({"databases": [{"name": "FAOSTAT"}]}, lambda q: [], log)
    assert ("Databases", ["FAOSTAT"]) in out and "unverified" in log[0]


def test_tier_players_under_current_names():
    from AiAgentsFleet.cmi_report import extras
    tiers = [{"players": ["Syngenta Group", "BASF SE"]},
             {"players": ["Novonesis (Novozymes)", "Valagro S.p.A. (Syngenta)", "Isagro Group"]},
             {"players": ["Italpollina"]}]
    check = {"companies": [{"name": "Novonesis (Novozymes)", "current": "Novonesis", "change": "rebranded"},
                           {"name": "Valagro S.p.A. (Syngenta)", "current": "Syngenta Group", "change": "acquired"},
                           {"name": "Isagro Group", "current": "Gowan Group", "change": "acquired"},
                           {"name": "Italpollina", "current": "HELLO NATURE", "change": "rebranded"}]}
    out = extras.current_names(tiers, check)
    assert out[1]["players"] == ["Novonesis (formerly Novozymes)", "Isagro Group (part of Gowan Group)"]
    assert out[2]["players"] == ["Hello Nature (formerly Italpollina)"]


def test_mea_abbreviation_added_once():
    from AiAgentsFleet.cmi_report import fmt
    assert fmt.mea("Middle East & Africa grows") == "Middle East & Africa (MEA) grows"
    assert fmt.mea("Middle East & Africa (MEA) grows") == "Middle East & Africa (MEA) grows"
    assert fmt.mea("the Middle East and Africa") == "the Middle East and Africa (MEA)"


def test_summary_opener_is_global_only():
    from AiAgentsFleet.cmi_report.build import global_only
    text = "Demand shifts to microbials. The Middle East & Africa carries $158M. MEA grows 9.7%. Growth is 11.2%."
    assert global_only(text) == "Demand shifts to microbials. Growth is 11.2%."


def test_factor_names_lose_their_figures():
    from AiAgentsFleet.cmi_report.extras import factor_name
    assert factor_name("High costs, especially feed accounting for ~69% of expenses") == "High costs"
    assert factor_name("Yield gains (5-20% field trial gains)") == "Yield gains"
    assert factor_name("Regulatory recognition (EU, US, India)") == "Regulatory recognition (EU, US, India)"
