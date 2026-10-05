"""Supply-side pre-estimation screen: distributor/retailer exclusion and
parent/brand dedupe (real double counting seen in Disposable Medical Gloves
and Olive Oil runs)."""
from __future__ import annotations

from types import SimpleNamespace

from me_engine.supply import graph as supply_graph
from me_engine.supply.companies import (
    Company,
    company_role,
    load_companies,
    normalize_company_name,
    save_companies_csv,
    screen_companies,
)
from me_engine.supply.estimator import CountryBreakdown
from me_engine.supply.quality import compute_company_quality_flags


def _c(name: str, role: str | None = None, parent: str | None = None) -> Company:
    facts = {}
    if role:
        facts["role"] = role
    if parent:
        facts["parent_company"] = parent
    return Company(name=name, facts=facts)


def test_normalize_strips_corporate_and_regional_suffixes():
    assert normalize_company_name("Medline International") == "medline"
    assert normalize_company_name("Medline Industries, LP") == "medline"
    assert normalize_company_name("Sovena Group") == "sovena"
    assert normalize_company_name("Borges International Group") == "borges"
    assert normalize_company_name("Borges Portugal") == "borges"
    assert normalize_company_name("Deoleo, S.A.") == "deoleo"
    assert normalize_company_name("Salov S.p.A.") == "salov"
    # never strips a name to nothing
    assert normalize_company_name("Group SA") == "group sa"


def test_distributors_and_retailers_excluded_with_reason():
    companies = [
        _c("Ansell", "manufacturer"),
        _c("Cardinal Health", "distributor"),
        _c("McKesson", "distributor"),
        _c("Henry Schein", "Distributor / wholesaler"),
        _c("Walmart", "retailer"),
        _c("Top Glove", "manufacturer"),
        _c("Unknown Role Co"),  # no role fact -> kept as-is
    ]
    kept, excluded, merged = screen_companies(companies)
    assert [c.name for c in kept] == ["Ansell", "Top Glove", "Unknown Role Co"]
    assert {e["name"] for e in excluded} == {"Cardinal Health", "McKesson", "Henry Schein", "Walmart"}
    assert all(e["reason"] and e["role"] in ("distributor", "retailer") for e in excluded)
    assert merged == []


def test_role_from_user_file_category_column():
    assert company_role(Company("A", {"category": "Manufacturers of Gloves"})) == "manufacturer"
    assert company_role(Company("B", {"Category": "Distributors of Gloves"})) == "distributor"
    assert company_role(Company("C", {"Role": "Manufacturer & distributor"})) == "manufacturer"
    assert company_role(Company("D", {"Functionality": "Distributes gloves"})) is None


def test_medline_regional_arms_merged():
    kept, _, merged = screen_companies([
        _c("Medline International", "manufacturer"),
        _c("Ansell", "manufacturer"),
        _c("Medline Industries", "manufacturer"),
    ])
    assert [c.name for c in kept] == ["Medline International", "Ansell"]
    assert merged == [{
        "kept": "Medline International", "merged": ["Medline Industries"],
        "reason": merged[0]["reason"],
    }]


def test_deoleo_brands_collapse_to_listed_parent():
    kept, _, merged = screen_companies([
        _c("Bertolli", "brand_owner", "Deoleo"),
        _c("Carbonell", "brand_owner", "Deoleo S.A."),
        _c("Deoleo", "manufacturer", "CVC Capital Partners"),
        _c("Carapelli", "brand_owner", "Deoleo"),
    ])
    assert [c.name for c in kept] == ["Deoleo"]
    assert sorted(merged[0]["merged"]) == ["Bertolli", "Carapelli", "Carbonell"]


def test_sovena_salov_dcoop_groups():
    kept, _, merged = screen_companies([
        _c("Sovena", "manufacturer"),
        _c("Sovena Group", "manufacturer"),
        _c("Andorinha", "brand_owner", "Sovena Group"),
        _c("Oliveira da Serra", "brand_owner", "Sovena"),
        _c("Filippo Berio", "brand_owner", "Salov"),
        _c("Salov", "manufacturer", "Bright Food"),
        _c("Hojiblanca", "brand_owner", "Dcoop"),
        _c("Dcoop", "manufacturer"),
        _c("Borges International Group", "manufacturer"),
        _c("Borges", "manufacturer"),
        _c("Borges Portugal", "manufacturer"),
        _c("Acesur", "manufacturer"),
    ])
    assert [c.name for c in kept] == ["Sovena", "Salov", "Dcoop", "Borges International Group", "Acesur"]
    by_kept = {m["kept"]: sorted(m["merged"]) for m in merged}
    assert by_kept["Sovena"] == ["Andorinha", "Oliveira da Serra", "Sovena Group"]
    assert by_kept["Salov"] == ["Filippo Berio"]
    assert by_kept["Dcoop"] == ["Hojiblanca"]
    assert by_kept["Borges International Group"] == ["Borges", "Borges Portugal"]


def test_independent_parent_value_is_not_a_parent():
    kept, _, merged = screen_companies([
        Company("Henkel", {"Parent_or_Independent": "Independent"}),
        Company("3M", {"Parent_or_Independent": "Independent"}),
    ])
    assert [c.name for c in kept] == ["Henkel", "3M"] and merged == []


def test_discovered_csv_roundtrip_keeps_role_and_parent(tmp_path):
    path = save_companies_csv([
        _c("Hartalega", "manufacturer"),
        _c("Cardinal Health", "distributor"),
        _c("Bertolli", "brand_owner", "Deoleo"),
        _c("Deoleo", "manufacturer"),
    ], tmp_path / "x.csv")
    kept, excluded, merged = screen_companies(load_companies(path))
    assert [c.name for c in kept] == ["Hartalega", "Deoleo"]
    assert [e["name"] for e in excluded] == ["Cardinal Health"]
    assert merged[0]["merged"] == ["Bertolli"]


def test_graph_screens_before_estimation_and_reports(monkeypatch):
    estimated: list[str] = []

    def fake_estimate(llm, market, company, countries, enable_web):
        estimated.append(company.name)
        return {c: 100.0 for c in countries}, "financial_api", []

    monkeypatch.setattr(supply_graph, "estimate_company_countries", fake_estimate)
    monkeypatch.setattr(supply_graph, "estimate_coverage_by_country",
                        lambda llm, m, countries, named: {c: 0.5 for c in countries})
    state = supply_graph.build_graph().invoke({
        "market_name": "Disposable Medical Gloves",
        "companies": [
            _c("Ansell", "manufacturer"), _c("McKesson", "distributor"),
            _c("Medline International", "manufacturer"), _c("Medline Industries", "manufacturer"),
        ],
        "config": SimpleNamespace(enable_web=False),
        "llm": object(),
        "countries": ["United States", "Germany"],
        "log": [],
    })
    assert estimated == ["Ansell", "Medline International"]
    assert [e["name"] for e in state["excluded_companies"]] == ["McKesson"]
    assert state["merged_companies"][0]["merged"] == ["Medline Industries"]
    assert state["grand_total_usd_mn"] == 800.0  # 2 companies x 100 / 0.5 x 2 countries
    assert set(state["country_breakdown"][0].company_shares) == {"Ansell", "Medline International"}


def test_high_gross_up_flag():
    breakdown = [
        CountryBreakdown("United States", 100.0, 0.8, 125.0, {}),
        CountryBreakdown("India", 10.0, 0.3, 33.3, {}),
    ]
    flags = compute_company_quality_flags({"A": "financial_api"}, 158.3, breakdown, {})
    codes = [f["code"] for f in flags]
    assert codes == ["high_gross_up"]
    assert "India 3.3x" in flags[0]["message"]
    # backward-compatible two-argument call still works
    assert compute_company_quality_flags({"A": "financial_api"}, 1.0) == []
