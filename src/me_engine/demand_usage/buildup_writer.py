"""Write the transparent build-up artifact: Geography -> Usage -> Driver ->
Quantity -> Price -> Value, line by line. This is the artifact Desk Research
fundamentally cannot produce, since it never breaks the total down this way.

Two output forms of the same data:
    - JSON: full machine-readable state (for programmatic reuse/comparison)
    - .xlsx: a human-readable workbook (Build-Up, Summary, Validation sheets)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

_PROV_FILL = {
    "verified_observed": PatternFill("solid", fgColor="C6EFCE"),  # green -- code-verified
    "derived": PatternFill("solid", fgColor="FFEB9C"),  # yellow -- deterministic formula
    # blue -- Phase 6 (DEMAND_SIDE_EVIDENCE_PLAN.md): a real, named-source
    # LLM claim that code could NOT independently verify -- deliberately
    # distinct from both "derived" (yellow) and "assumption" (red) so it
    # never reads as either stronger or weaker than it actually is.
    "source_found_unverified": PatternFill("solid", fgColor="D9D2E9"),
    "assumption": PatternFill("solid", fgColor="FFC7CE"),  # red -- no evidence, no derivation
}


def build_report_dict(state: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten the graph state into the auditable build-up chain plus metadata."""
    buckets = state.get("usage_buckets", []) or []
    countries = state.get("countries", []) or []
    drivers = state.get("drivers", {}) or {}
    quantities = state.get("quantities", {}) or {}
    prices = state.get("prices", {}) or {}
    usage_values = state.get("usage_values", {}) or {}
    driver_facts = state.get("driver_facts", {}) or {}
    price_facts = state.get("price_facts", {}) or {}
    unit_contract = state.get("unit_contract", {}) or {}

    rows: list[dict[str, Any]] = []
    for b in buckets:
        name = b["name"]
        driver = drivers.get(name, {})
        price = prices.get(name, {})
        price_fact = price_facts.get(name, {})
        contract = unit_contract.get(name, {})
        for c in countries:
            qty = quantities.get(name, {}).get(c, {})
            facts = driver_facts.get(name, {}).get(c, {})
            driver_raw = facts.get("driver_raw", {})
            intensity = facts.get("intensity", {})
            rows.append({
                "country": c,
                "usage_bucket": name,
                "bucket_category": b.get("bucket_category", "physical_consumption"),
                "usage_description": b.get("description", ""),
                "driver_name": driver.get("driver_name", ""),
                "driver_unit": driver.get("unit", ""),
                "final_demand_unit": driver.get("final_demand_unit", ""),
                "conversion_formula": facts.get("formula") or driver.get("conversion_formula", ""),
                "library_status": facts.get("library_status") or driver.get("library_status", ""),
                "quantity": qty.get("value"),
                "provenance": qty.get("provenance"),
                "provenance_note": qty.get("note", ""),
                "driver_evidence_type": driver_raw.get("evidence_type", ""),
                "driver_source": driver_raw.get("source", ""),
                "driver_source_url": driver_raw.get("source_url", ""),
                "driver_citation": driver_raw.get("citation", ""),
                "driver_year": driver_raw.get("year", ""),
                "intensity_evidence_type": intensity.get("evidence_type", ""),
                "asp_usd_per_unit": price.get("asp_usd_per_unit"),
                "asp_evidence_type": price.get("evidence_type", ""),
                "asp_source": price_fact.get("source", ""),
                "asp_source_url": price_fact.get("source_url", ""),
                "asp_citation": price_fact.get("citation", ""),
                "value_usd_mn": usage_values.get(name, {}).get(c),
                "llm_call_failed": bool(qty.get("call_failed")) or bool(price.get("call_failed")),
                "unit_contract_status": contract.get("status", "unknown"),
                "normalized_asp_per_base_unit": contract.get("normalized_asp_per_base_unit"),
            })

    return {
        "market_name": state.get("market_name"),
        "methodology": "demand_side_usage",
        # Approved Phase 1 Market Setup -- carried through unchanged, never
        # re-derived by Phase 3 (spec section 18: "Market Setup" section).
        "market_setup": {
            "scope": state.get("scope"),
            "geography": countries,
            "base_year": state.get("base_year"),
            "market_unit": state.get("market_unit"),
            "physical_unit": state.get("physical_unit"),
        },
        "countries": countries,
        "usage_buckets": buckets,
        # Non-physical (financial/inventory) buckets excluded before pricing --
        # kept here for audit, never silently dropped from the record.
        "excluded_usage_buckets": state.get("excluded_usage_buckets", []),
        "usage_classifications": state.get("usage_classifications", {}),
        # Scope -> Taxonomy validation (flag only, evaluated before approval --
        # a bucket flagged ambiguous/out_of_scope was NOT auto-removed).
        "scope_validation_flags": state.get("scope_validation_flags", {}),
        # Phase 9 (DEMAND_SIDE_EVIDENCE_PLAN.md) -- the aggregate "scope too
        # narrow for this taxonomy" signal plus any explicit user overrides
        # that let an out_of_scope bucket into sizing anyway.
        "scope_taxonomy_aggregate": state.get("scope_taxonomy_aggregate", {}),
        "scope_overrides": state.get("scope_overrides", {}),
        "scope_amendment_text": state.get("scope_amendment_text", ""),
        # Candidate major physical applications the bucket set may be missing --
        # flag only, evaluated before calculation, never merged into usage_buckets.
        "missing_usage_flags": state.get("missing_usage_flags", []),
        # Deterministic unit-contract verdict per bucket (units.py, no LLM) --
        # any bucket with status != "ok" was excluded from calculation entirely.
        "unit_contract": unit_contract,
        "unit_rejected_buckets": state.get("unit_rejected_buckets", []),
        "drivers": drivers,
        # Phase 7 (`DEMAND_SIDE_EVIDENCE_PLAN.md`) -- the explicit, auditable
        # per-driver Evidence Strategy (what to look for, where, how it could
        # be derived) approved BEFORE evidence retrieval began -- surfaced
        # here so the audit trail shows the plan, not just the outcome. A
        # driver with no strategy on record reports "(no explicit strategy
        # recorded)" rather than being silently omitted.
        "evidence_strategies": {
            name: {
                "strategy": driver.get("evidence_strategy"),
                "flags": driver.get("evidence_strategy_flags", []),
            }
            for name, driver in drivers.items()
        },
        "build_up_rows": rows,
        # Evidence Retrieval Plans -- the full source_priority -> search_query
        # -> source_URL -> extracted_fact chain, EVERY attempt persisted (not
        # just the winning source), for all three quantitative inputs.
        "retrieval_plans": state.get("retrieval_plans", {}),
        "retrieval_attempts": state.get("retrieval_attempts", {}),
        "intensity_retrieval_plans": state.get("intensity_retrieval_plans", {}),
        "intensity_retrieval_attempts": state.get("intensity_retrieval_attempts", {}),
        "price_retrieval_plans": state.get("price_retrieval_plans", {}),
        "price_retrieval_attempts": state.get("price_retrieval_attempts", {}),
        # Price-basis resolution (spec section 8/12) -- when the priced unit
        # differs from final_demand_unit, the bridging conversion factor and
        # its own evidence chain are recorded here, never silently assumed.
        "price_basis_records": state.get("price_basis_records", {}),
        # Phase 8 (`DEMAND_SIDE_EVIDENCE_PLAN.md`) -- pricing's own explicit,
        # auditable Evidence Strategy (same shape/spirit as the driver
        # `evidence_strategies` above) plus the full per-bucket price record
        # (raw/normalized price, verification status) -- surfaced here so a
        # reviewer sees the pricing search PLAN and its outcome, not just
        # the final ASP number.
        "price_evidence_strategies": state.get("price_evidence_strategies", {}),
        "prices": prices,
        "pricing_conversion_factors": state.get("pricing_conversion_factors", {}),
        "price_conversion_retrieval_attempts": state.get("price_conversion_retrieval_attempts", {}),
        "country_totals_usd_mn": state.get("country_totals"),
        "total_market_value_usd_mn": state.get("total_market_value"),  # Included Demand (physical consumption only)
        "related_demand_country_totals_usd_mn": state.get("related_demand_country_totals"),
        "related_demand_value_usd_mn": state.get("related_demand_value"),  # inventory/reserve, kept separate
        "validation": state.get("validation"),
        "evidence_quality": (state.get("validation") or {}).get("evidence_quality"),
        # Flag-only driver-magnitude cross-check + informational external
        # benchmark sweep (spec sections 11/16) -- never used to adjust any
        # quantity or the final total; see magnitude.py / triangulation.py.
        "driver_magnitude_flags": state.get("driver_magnitude_flags", []),
        "triangulation": state.get("triangulation", []),
        "triangulation_large_gap_flags": state.get("triangulation_large_gap_flags", []),
        # Phase 11 (DEMAND_SIDE_EVIDENCE_PLAN.md) -- generic magnitude/cross-
        # source validation (driver/intensity/derived-quantity/price
        # magnitude, country-vs-parent, usage-vs-parent-category, year/period
        # consistency). Flag-only, additive alongside the two fields above --
        # see magnitude_validation.py.
        "magnitude_validation_flags": state.get("magnitude_validation_flags", []),
        "magnitude_validation_records": state.get("magnitude_validation_records", []),
        "quality_flags": state.get("quality_flags"),
        "log": state.get("log"),
    }


def write_buildup_json(state: Mapping[str, Any], out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(build_report_dict(state), indent=2, ensure_ascii=False), encoding="utf-8")
    return out_path


def write_buildup_workbook(state: Mapping[str, Any], out_path: Path) -> Path:
    report = build_report_dict(state)
    wb = Workbook()
    bold = Font(bold=True)

    setup_ws = wb.active
    setup_ws.title = "Market Setup"
    setup = report.get("market_setup") or {}
    setup_rows = [
        ("Market Name", report.get("market_name")),
        ("Scope", setup.get("scope")),
        ("Geography", ", ".join(setup.get("geography") or [])),
        ("Base Year", setup.get("base_year")),
        ("Market Unit", setup.get("market_unit")),
        ("Physical Unit", setup.get("physical_unit")),
    ]
    for i, (label, value) in enumerate(setup_rows, start=1):
        setup_ws.cell(i, 1, label).font = bold
        setup_ws.cell(i, 2, value)

    ws = wb.create_sheet("Build-Up", 0)
    ws.title = "Build-Up"
    ws.cell(1, 1, "Market Name").font = bold
    ws.cell(1, 2, report["market_name"])
    ws.cell(2, 1, "Methodology").font = bold
    ws.cell(2, 2, "Demand-Side (Usage/Application, bottom-up, evidence-first, human-approved setup+taxonomy V3)")
    headers = [
        "Country", "Usage Bucket", "Bucket Category", "Driver", "Driver Unit", "Final Demand Unit",
        "Conversion Formula", "Library Status", "Quantity", "Provenance", "Provenance Note",
        "Driver Evidence Type", "Driver Source", "Driver Source URL", "Driver Citation", "Driver Year",
        "Intensity Evidence Type", "ASP (US$/unit)", "ASP Evidence Type", "ASP Source", "ASP Source URL",
        "ASP Citation", "Value (US$ Mn)", "LLM Call Failed",
        "Unit Contract Status", "Normalized ASP (per base unit)",
    ]
    for ci, h in enumerate(headers, start=1):
        ws.cell(4, ci, h).font = bold
    r = 5
    for row in report["build_up_rows"]:
        ws.cell(r, 1, row["country"])
        ws.cell(r, 2, row["usage_bucket"])
        cat_cell = ws.cell(r, 3, row.get("bucket_category", "physical_consumption"))
        if row.get("bucket_category") == "inventory_reserve":
            cat_cell.fill = PatternFill("solid", fgColor="BDD7EE")  # distinct blue -- Related, not Included
        ws.cell(r, 4, row["driver_name"])
        ws.cell(r, 5, row["driver_unit"])
        ws.cell(r, 6, row["final_demand_unit"])
        ws.cell(r, 7, row["conversion_formula"])
        ws.cell(r, 8, row["library_status"])
        ws.cell(r, 9, row["quantity"])
        prov_cell = ws.cell(r, 10, row["provenance"])
        if row["provenance"] in _PROV_FILL:
            prov_cell.fill = _PROV_FILL[row["provenance"]]
        ws.cell(r, 11, row["provenance_note"])
        driver_et_cell = ws.cell(r, 12, row["driver_evidence_type"])
        if row["driver_evidence_type"] in _PROV_FILL:
            driver_et_cell.fill = _PROV_FILL[row["driver_evidence_type"]]
        ws.cell(r, 13, row["driver_source"])
        ws.cell(r, 14, row.get("driver_source_url", ""))
        ws.cell(r, 15, row["driver_citation"])
        ws.cell(r, 16, row["driver_year"])
        ws.cell(r, 17, row["intensity_evidence_type"])
        ws.cell(r, 18, row["asp_usd_per_unit"])
        asp_et_cell = ws.cell(r, 19, row["asp_evidence_type"])
        if row["asp_evidence_type"] in _PROV_FILL:
            asp_et_cell.fill = _PROV_FILL[row["asp_evidence_type"]]
        ws.cell(r, 20, row["asp_source"])
        ws.cell(r, 21, row.get("asp_source_url", ""))
        ws.cell(r, 22, row["asp_citation"])
        ws.cell(r, 23, row["value_usd_mn"])
        fail_cell = ws.cell(r, 24, "YES -- NOT a genuine estimate" if row["llm_call_failed"] else "")
        if row["llm_call_failed"]:
            fail_cell.fill = PatternFill("solid", fgColor="FF0000")
        uc_status = row.get("unit_contract_status", "unknown")
        uc_cell = ws.cell(r, 25, uc_status)
        if uc_status != "ok":
            uc_cell.fill = PatternFill("solid", fgColor="FF0000")
        ws.cell(r, 26, row.get("normalized_asp_per_base_unit"))
        r += 1

    excluded_ws = wb.create_sheet("Excluded Usages")
    excluded_ws.cell(1, 1, "Bucket").font = bold
    excluded_ws.cell(1, 2, "Description").font = bold
    excluded_ws.cell(1, 3, "Status").font = bold
    excluded_ws.cell(1, 4, "Classification").font = bold
    excluded_ws.cell(1, 5, "Reason").font = bold
    excluded_ws.cell(1, 6, "Confidence").font = bold
    er = 2
    for exc in report.get("excluded_usage_buckets") or []:
        excluded_ws.cell(er, 1, exc.get("name", ""))
        excluded_ws.cell(er, 2, exc.get("description", ""))
        status_cell = excluded_ws.cell(er, 3, exc.get("status", "excluded_non_physical_usage"))
        status_cell.fill = PatternFill("solid", fgColor="FFC7CE")
        excluded_ws.cell(er, 4, exc.get("classification", ""))
        excluded_ws.cell(er, 5, exc.get("reason", ""))
        excluded_ws.cell(er, 6, exc.get("confidence", ""))
        er += 1
    if er == 2:
        excluded_ws.cell(2, 1, "(none -- every discovered usage bucket was classified as a genuine physical end-use)")

    scope_ws = wb.create_sheet("Scope Validation")
    scope_agg = report.get("scope_taxonomy_aggregate") or {}
    scope_overrides = report.get("scope_overrides") or {}
    sr2 = 1
    if scope_agg.get("likely_scope_too_narrow"):
        warn_cell = scope_ws.cell(sr2, 1, (
            f"SCOPE LIKELY TOO NARROW FOR THIS TAXONOMY -- {scope_agg.get('flagged_count', 0)}/"
            f"{scope_agg.get('total_buckets', 0)} buckets ({float(scope_agg.get('flagged_ratio', 0) or 0):.0%}) "
            "flagged ambiguous/out-of-scope. Consider revising the Market Setup scope statement."
        ))
        warn_cell.font = bold
        warn_cell.fill = PatternFill("solid", fgColor="FFC7CE")
        sr2 += 2
    if scope_overrides:
        override_cell = scope_ws.cell(sr2, 1, (
            f"{len(scope_overrides)} out-of-scope bucket(s) explicitly retained by user override: "
            + "; ".join(f"{name} -- {why}" for name, why in scope_overrides.items())
        ))
        override_cell.font = bold
        override_cell.fill = PatternFill("solid", fgColor="D9D2E9")
        sr2 += 2

    header_row = sr2
    scope_ws.cell(header_row, 1, "Usage Bucket").font = bold
    scope_ws.cell(header_row, 2, "Classification").font = bold
    scope_ws.cell(header_row, 3, "Reason").font = bold
    scope_ws.cell(header_row, 4, "Evidence").font = bold
    scope_ws.cell(header_row, 5, "Confidence").font = bold
    scope_ws.cell(header_row, 6, "Source").font = bold
    sr2 = header_row + 1
    first_data_row = sr2
    for bucket_name, flag in (report.get("scope_validation_flags") or {}).items():
        scope_ws.cell(sr2, 1, bucket_name)
        classification = flag.get("classification", "in_scope")
        cls_cell = scope_ws.cell(sr2, 2, classification)
        if classification != "in_scope":
            cls_cell.fill = PatternFill("solid", fgColor="FFEB9C" if classification == "ambiguous" else "FFC7CE")
        scope_ws.cell(sr2, 3, flag.get("reason", ""))
        scope_ws.cell(sr2, 4, flag.get("evidence", ""))
        scope_ws.cell(sr2, 5, flag.get("confidence", ""))
        scope_ws.cell(sr2, 6, flag.get("source", ""))
        sr2 += 1
    if sr2 == first_data_row:
        scope_ws.cell(first_data_row, 1, "(no scope validation flags recorded)")

    coverage_ws = wb.create_sheet("Coverage Review")
    coverage_ws.cell(1, 1, "Candidate Missing Application").font = bold
    coverage_ws.cell(1, 2, "Reason").font = bold
    coverage_ws.cell(1, 3, "Evidence Type").font = bold
    coverage_ws.cell(1, 4, "Evidence Snippet").font = bold
    coverage_ws.cell(1, 5, "Confidence").font = bold
    coverage_ws.cell(1, 6, "Max Similarity to Existing Bucket").font = bold
    cr = 2
    for m in report.get("missing_usage_flags") or []:
        coverage_ws.cell(cr, 1, m.get("name", ""))
        coverage_ws.cell(cr, 2, m.get("reason", ""))
        et_cell = coverage_ws.cell(cr, 3, m.get("evidence_type", ""))
        et_cell.fill = PatternFill(
            "solid", fgColor="C6EFCE" if m.get("evidence_type") == "evidence_backed" else "FFEB9C"
        )
        coverage_ws.cell(cr, 4, m.get("evidence_snippet", ""))
        coverage_ws.cell(cr, 5, m.get("confidence", ""))
        coverage_ws.cell(cr, 6, m.get("max_similarity_to_existing", ""))
        cr += 1
    if cr == 2:
        coverage_ws.cell(2, 1, "(none -- no evidence-supported coverage gaps were found; NOT proof of full coverage)")

    retrieval_ws = wb.create_sheet("Evidence Retrieval Plan")
    retrieval_headers = [
        "Kind", "Usage Bucket", "Geography", "Source Priority Rank", "Source Name", "Source Type",
        "Search Query / Locator", "Num Results", "Found", "Source URL / Document",
        "Page / Sheet / Field Ref", "Value", "Unit", "Year", "Citation", "Evidence Type", "Rejection Reason",
    ]
    for ci, h in enumerate(retrieval_headers, start=1):
        retrieval_ws.cell(1, ci, h).font = bold

    def _location_ref(a: dict[str, Any]) -> str:
        if a.get("page_number"):
            return f"page {a['page_number']}"
        if a.get("location_ref"):
            return str(a["location_ref"])
        return ""

    def _write_retrieval_rows(start_row: int, kind_label: str, attempts_by_bucket: dict) -> int:
        rr = start_row
        for bucket_name, per_geo in (attempts_by_bucket or {}).items():
            for geo, attempts in per_geo.items():
                for a in attempts:
                    retrieval_ws.cell(rr, 1, kind_label)
                    retrieval_ws.cell(rr, 2, bucket_name)
                    retrieval_ws.cell(rr, 3, geo)
                    retrieval_ws.cell(rr, 4, a.get("rank"))
                    retrieval_ws.cell(rr, 5, a.get("source_name", ""))
                    retrieval_ws.cell(rr, 6, a.get("source_type", ""))
                    retrieval_ws.cell(rr, 7, a.get("search_query", ""))
                    retrieval_ws.cell(rr, 8, a.get("num_results", 0))
                    found_cell = retrieval_ws.cell(rr, 9, "YES" if a.get("found") else "no")
                    found_cell.fill = PatternFill("solid", fgColor="C6EFCE" if a.get("found") else "FFC7CE")
                    retrieval_ws.cell(rr, 10, a.get("source_url", ""))
                    retrieval_ws.cell(rr, 11, _location_ref(a))
                    retrieval_ws.cell(rr, 12, a.get("value", ""))
                    retrieval_ws.cell(rr, 13, a.get("unit", ""))
                    retrieval_ws.cell(rr, 14, a.get("year", ""))
                    retrieval_ws.cell(rr, 15, a.get("citation", ""))
                    et_cell = retrieval_ws.cell(rr, 16, a.get("evidence_type", ""))
                    if a.get("evidence_type") in _PROV_FILL:
                        et_cell.fill = _PROV_FILL[a["evidence_type"]]
                    retrieval_ws.cell(rr, 17, a.get("rejection_reason", ""))
                    rr += 1
        return rr

    rr = 2
    rr = _write_retrieval_rows(rr, "Driver", report.get("retrieval_attempts"))
    rr = _write_retrieval_rows(rr, "Intensity", report.get("intensity_retrieval_attempts"))
    rr = _write_retrieval_rows(rr, "Price", report.get("price_retrieval_attempts"))
    rr = _write_retrieval_rows(rr, "Price Conversion Factor", report.get("price_conversion_retrieval_attempts"))
    if rr == 2:
        retrieval_ws.cell(2, 1, "(no retrieval attempts recorded)")

    # Per-market evidence-source-type counters (spec section 16: "number of
    # web sources / PDFs parsed / CSV-XLSX datasets consumed / API facts
    # retrieved / evidence attempts / rejected sources and why").
    all_attempts = [
        a
        for attempts_by_bucket in (
            report.get("retrieval_attempts"), report.get("intensity_retrieval_attempts"),
            report.get("price_retrieval_attempts"), report.get("price_conversion_retrieval_attempts"),
        )
        for per_geo in (attempts_by_bucket or {}).values()
        for attempts in per_geo.values()
        for a in attempts
    ]
    counters_ws = wb.create_sheet("Evidence Source Counters")
    counters_ws.cell(1, 1, "Metric").font = bold
    counters_ws.cell(1, 2, "Count").font = bold
    by_type = {}
    for a in all_attempts:
        t = a.get("source_type", "web_page")
        by_type.setdefault(t, {"attempts": 0, "found": 0})
        by_type[t]["attempts"] += 1
        if a.get("found"):
            by_type[t]["found"] += 1
    rejection_counts: dict[str, int] = {}
    for a in all_attempts:
        if not a.get("found") and a.get("rejection_reason"):
            rejection_counts[a["rejection_reason"]] = rejection_counts.get(a["rejection_reason"], 0) + 1
    cw_r = 2
    counters_ws.cell(cw_r, 1, "Total evidence attempts (all kinds/types)")
    counters_ws.cell(cw_r, 2, len(all_attempts))
    cw_r += 1
    for t, c in sorted(by_type.items()):
        counters_ws.cell(cw_r, 1, f"{t}: attempts / found")
        counters_ws.cell(cw_r, 2, f"{c['attempts']} / {c['found']}")
        cw_r += 1
    cw_r += 1
    counters_ws.cell(cw_r, 1, "Rejection reasons (misses only)").font = bold
    cw_r += 1
    for reason, count in sorted(rejection_counts.items(), key=lambda kv: -kv[1]):
        counters_ws.cell(cw_r, 1, reason)
        counters_ws.cell(cw_r, 2, count)
        cw_r += 1

    magnitude_ws = wb.create_sheet("Driver Magnitude Checks")
    magnitude_ws.cell(1, 1, "Bucket").font = bold
    magnitude_ws.cell(1, 2, "Used Value").font = bold
    magnitude_ws.cell(1, 3, "Used Unit").font = bold
    magnitude_ws.cell(1, 4, "Independent Reference Value").font = bold
    magnitude_ws.cell(1, 5, "Reference Source").font = bold
    magnitude_ws.cell(1, 6, "Ratio").font = bold
    magnitude_ws.cell(1, 7, "Note").font = bold
    mr = 2
    for f in report.get("driver_magnitude_flags") or []:
        magnitude_ws.cell(mr, 1, f.get("bucket", ""))
        magnitude_ws.cell(mr, 2, f.get("used_value", ""))
        magnitude_ws.cell(mr, 3, f.get("used_unit", ""))
        magnitude_ws.cell(mr, 4, f.get("reference_value", ""))
        magnitude_ws.cell(mr, 5, f.get("reference_source", ""))
        ratio_cell = magnitude_ws.cell(mr, 6, f.get("ratio", ""))
        ratio_cell.fill = PatternFill("solid", fgColor="FFC7CE")
        magnitude_ws.cell(mr, 7, f.get("note", ""))
        mr += 1
    if mr == 2:
        magnitude_ws.cell(2, 1, "(none -- no bucket's driver total diverged 3x+ from an independent reference)")

    tri_ws = wb.create_sheet("Triangulation")
    tri_ws.cell(1, 1, "Benchmark Category").font = bold
    tri_ws.cell(1, 2, "Value").font = bold
    tri_ws.cell(1, 3, "Unit").font = bold
    tri_ws.cell(1, 4, "Year").font = bold
    tri_ws.cell(1, 5, "Source").font = bold
    tri_ws.cell(1, 6, "Citation").font = bold
    tri_ws.cell(1, 7, "Evidence Type").font = bold
    tri_ws.cell(1, 8, "Demand-Side Estimate (US$ Mn)").font = bold
    tri_ws.cell(1, 9, "Gap %").font = bold
    tr = 2
    for b in report.get("triangulation") or []:
        tri_ws.cell(tr, 1, b.get("category", ""))
        tri_ws.cell(tr, 2, b.get("value", ""))
        tri_ws.cell(tr, 3, b.get("unit", ""))
        tri_ws.cell(tr, 4, b.get("year", ""))
        tri_ws.cell(tr, 5, b.get("source", ""))
        tri_ws.cell(tr, 6, b.get("citation", ""))
        et_cell = tri_ws.cell(tr, 7, b.get("evidence_type", ""))
        if b.get("evidence_type") in _PROV_FILL:
            et_cell.fill = _PROV_FILL[b["evidence_type"]]
        tri_ws.cell(tr, 8, b.get("demand_side_estimate_usd_mn", ""))
        gap_cell = tri_ws.cell(tr, 9, b.get("gap_pct", ""))
        if isinstance(b.get("gap_pct"), (int, float)) and abs(b["gap_pct"]) >= 40.0:
            gap_cell.fill = PatternFill("solid", fgColor="FFEB9C")
        tr += 1
    if tr == 2:
        tri_ws.cell(2, 1, "(no independent benchmark evidence found -- informational sheet only, "
                          "never used to adjust the bottom-up total)")

    # Phase 11 (DEMAND_SIDE_EVIDENCE_PLAN.md) -- generic magnitude/cross-
    # source validation. Flag-only; never adjusts any value -- see
    # magnitude_validation.py.
    mcv_ws = wb.create_sheet("Magnitude Cross-Validation")
    mcv_headers = [
        "Check Type", "Bucket", "Subject", "Original Value", "Unit", "Original Source",
        "Original Verification Status", "Best Reference Value", "Reference Source",
        "Reference Verification Status", "Ratio", "Classification", "References Agree", "Note",
    ]
    for ci, h in enumerate(mcv_headers, start=1):
        mcv_ws.cell(1, ci, h).font = bold
    mv_r = 2
    for f in report.get("magnitude_validation_flags") or []:
        refs = f.get("references") or []
        best_ref = max(refs, key=lambda r: r.get("ratio") or 0) if refs else {}
        mcv_ws.cell(mv_r, 1, f.get("check_type", ""))
        mcv_ws.cell(mv_r, 2, f.get("bucket", ""))
        mcv_ws.cell(mv_r, 3, f.get("subject_label", ""))
        mcv_ws.cell(mv_r, 4, f.get("original_value", ""))
        mcv_ws.cell(mv_r, 5, f.get("original_unit", ""))
        mcv_ws.cell(mv_r, 6, f.get("original_source", ""))
        ovs_cell = mcv_ws.cell(mv_r, 7, f.get("original_verification_status", ""))
        if f.get("original_verification_status") in _PROV_FILL:
            ovs_cell.fill = _PROV_FILL[f["original_verification_status"]]
        mcv_ws.cell(mv_r, 8, best_ref.get("value", f.get("parent_total", "")))
        mcv_ws.cell(mv_r, 9, best_ref.get("source", f.get("parent_label", "")))
        rvs_cell = mcv_ws.cell(mv_r, 10, best_ref.get("verification_status", ""))
        if best_ref.get("verification_status") in _PROV_FILL:
            rvs_cell.fill = _PROV_FILL[best_ref["verification_status"]]
        ratio_cell = mcv_ws.cell(mv_r, 11, f.get("ratio", f.get("parent_ratio", "")))
        ratio_cell.fill = PatternFill("solid", fgColor="FFC7CE")
        mcv_ws.cell(mv_r, 12, f.get("classification", ""))
        mcv_ws.cell(mv_r, 13, f.get("references_agree", ""))
        mcv_ws.cell(mv_r, 14, "; ".join(f.get("notes") or []))
        mv_r += 1
    if mv_r == 2:
        mcv_ws.cell(2, 1, "(none -- no driver/intensity/derived-quantity/price magnitude, "
                          "country-vs-parent, usage-vs-parent-category, or year/period check was "
                          "flagged this run)")

    strategy_ws = wb.create_sheet("Evidence Strategy")
    strategy_headers = [
        "Usage Bucket", "Metric Required", "Physical Unit", "Period", "Geography",
        "Primary Source Types", "Preferred Sources", "Search Query Templates",
        "Fallback Source Types", "Fallback Queries", "Derivation Path", "Expected Evidence",
        "Why This Driver Measures The Usage", "Strategy Source", "Validation Flags",
    ]
    for ci, h in enumerate(strategy_headers, start=1):
        strategy_ws.cell(1, ci, h).font = bold
    st_r = 2
    for bucket_name, entry in (report.get("evidence_strategies") or {}).items():
        strategy = entry.get("strategy")
        flags = entry.get("flags") or []
        strategy_ws.cell(st_r, 1, bucket_name)
        if not strategy:
            strategy_ws.cell(st_r, 2, "(no explicit strategy recorded)")
            st_r += 1
            continue
        strategy_ws.cell(st_r, 2, strategy.get("metric_required", ""))
        strategy_ws.cell(st_r, 3, strategy.get("physical_unit", ""))
        strategy_ws.cell(st_r, 4, strategy.get("period", ""))
        strategy_ws.cell(st_r, 5, ", ".join(strategy.get("geography") or []))
        strategy_ws.cell(st_r, 6, ", ".join(strategy.get("primary_source_types") or []))
        strategy_ws.cell(st_r, 7, ", ".join(strategy.get("preferred_sources") or []))
        strategy_ws.cell(st_r, 8, " | ".join(strategy.get("search_query_templates") or []))
        strategy_ws.cell(st_r, 9, ", ".join(strategy.get("fallback_source_types") or []))
        strategy_ws.cell(st_r, 10, " | ".join(strategy.get("fallback_queries") or []))
        strategy_ws.cell(st_r, 11, strategy.get("derivation_path", ""))
        strategy_ws.cell(st_r, 12, strategy.get("expected_evidence", ""))
        strategy_ws.cell(st_r, 13, strategy.get("why_this_driver_measures_the_usage", ""))
        strategy_ws.cell(st_r, 14, strategy.get("strategy_source", ""))
        flags_cell = strategy_ws.cell(st_r, 15, "; ".join(f.get("code", "") for f in flags))
        if flags:
            flags_cell.fill = PatternFill("solid", fgColor="FFEB9C")  # yellow -- needs human review, not blocking
        st_r += 1
    if st_r == 2:
        strategy_ws.cell(2, 1, "(no evidence strategies recorded)")

    basis_ws = wb.create_sheet("Price Basis")
    basis_headers = [
        "Usage Bucket", "Final Demand Unit", "Priced Unit", "Same As Final Unit",
        "Conversion Factor Name", "Conversion Factor Value", "Conversion Factor Unit",
        "Conversion Evidence Type", "Rationale", "Price Basis Flags",
        "Normalized Price", "Normalized Unit", "Price Verification Status",
    ]
    for ci, h in enumerate(basis_headers, start=1):
        basis_ws.cell(1, ci, h).font = bold
    br = 2
    price_basis_records = report.get("price_basis_records") or {}
    conversion_factors = report.get("pricing_conversion_factors") or {}
    price_evidence_strategies = report.get("price_evidence_strategies") or {}
    prices = report.get("prices") or {}
    for bucket_name, driver in (report.get("drivers") or {}).items():
        basis = price_basis_records.get(bucket_name)
        if not basis:
            continue
        conv = conversion_factors.get(bucket_name)
        norm = (prices.get(bucket_name) or {}).get("price_normalization") or {}
        flags = (price_evidence_strategies.get(bucket_name) or {}).get("flags", []) or basis.get("flags", []) or []
        basis_ws.cell(br, 1, bucket_name)
        basis_ws.cell(br, 2, driver.get("final_demand_unit", ""))
        basis_ws.cell(br, 3, basis.get("price_basis_unit", ""))
        basis_ws.cell(br, 4, "YES" if basis.get("same_as_final_unit") else "no")
        basis_ws.cell(br, 5, basis.get("conversion_factor_name", ""))
        basis_ws.cell(br, 6, conv.get("value") if conv else "")
        basis_ws.cell(br, 7, basis.get("conversion_factor_unit", ""))
        et_cell = basis_ws.cell(br, 8, conv.get("evidence_type") if conv else "")
        if conv and conv.get("evidence_type") in _PROV_FILL:
            et_cell.fill = _PROV_FILL[conv["evidence_type"]]
        basis_ws.cell(br, 9, basis.get("rationale", ""))
        flags_cell = basis_ws.cell(br, 10, "; ".join(f.get("code", "") for f in flags))
        if flags:
            flags_cell.fill = PatternFill("solid", fgColor="FFEB9C")  # yellow -- needs human review, not blocking
        basis_ws.cell(br, 11, norm.get("normalized_price"))
        basis_ws.cell(br, 12, norm.get("normalized_unit", ""))
        vs_cell = basis_ws.cell(br, 13, norm.get("verification_status", ""))
        if norm.get("verification_status") in _PROV_FILL:
            vs_cell.fill = _PROV_FILL[norm["verification_status"]]
        br += 1
    if br == 2:
        basis_ws.cell(2, 1, "(no price-basis records -- priced unit equalled final_demand_unit for every bucket)")

    uc_ws = wb.create_sheet("Unit Contract")
    uc_ws.cell(1, 1, "Bucket").font = bold
    uc_ws.cell(1, 2, "Status").font = bold
    uc_ws.cell(1, 3, "Final Demand Unit (raw)").font = bold
    uc_ws.cell(1, 4, "Parsed Scale").font = bold
    uc_ws.cell(1, 5, "Parsed Base Unit").font = bold
    uc_ws.cell(1, 6, "Parsed Period").font = bold
    uc_ws.cell(1, 7, "Normalized ASP (per base unit)").font = bold
    uc_ws.cell(1, 8, "Reason (if rejected)").font = bold
    ucr = 2
    for name, c in (report.get("unit_contract") or {}).items():
        uc_ws.cell(ucr, 1, name)
        status_cell = uc_ws.cell(ucr, 2, c.get("status", "unknown"))
        status_cell.fill = PatternFill("solid", fgColor="C6EFCE" if c.get("status") == "ok" else "FFC7CE")
        uc_ws.cell(ucr, 3, c.get("final_unit_raw", ""))
        uc_ws.cell(ucr, 4, c.get("parsed_scale"))
        uc_ws.cell(ucr, 5, c.get("parsed_base_unit"))
        uc_ws.cell(ucr, 6, c.get("parsed_period"))
        uc_ws.cell(ucr, 7, c.get("normalized_asp_per_base_unit"))
        uc_ws.cell(ucr, 8, c.get("reason", ""))
        ucr += 1
    if ucr == 2:
        uc_ws.cell(2, 1, "(no buckets processed)")

    summary = wb.create_sheet("Summary")
    validation_for_summary = report.get("validation") or {}
    summary.cell(1, 1, "Reported Market View Breakdown").font = bold
    summary.cell(2, 1, "Included Demand (physical consumption)")
    summary.cell(2, 2, report["total_market_value_usd_mn"])
    summary.cell(3, 1, "+ Residual / Uncovered (implied by coverage gap -- NOT evidence-built, NOT in any bucket)")
    summary.cell(3, 2, validation_for_summary.get("residual_implied_usd_mn"))
    summary.cell(4, 1, "+ Related Demand (inventory/reserve, reported separately)")
    summary.cell(4, 2, report.get("related_demand_value_usd_mn"))
    summary.cell(5, 1, "= Reported Market View").font = bold
    summary.cell(5, 2, validation_for_summary.get("reported_market_view_usd_mn")).font = bold
    summary.cell(6, 1, "Estimated Coverage % (bounded pipeline estimate -- NOT verified market knowledge)")
    cov = validation_for_summary.get("coverage_pct")
    summary.cell(6, 2, f"{cov:.1f}%" if isinstance(cov, (int, float)) else "n/a")
    summary.cell(7, 1, "Coverage threshold (configurable)")
    thr = validation_for_summary.get("coverage_threshold_pct")
    summary.cell(7, 2, f"{thr:.0f}%" if isinstance(thr, (int, float)) else "n/a")

    summary.cell(9, 1, "Evidence Quality").font = bold
    eq = report.get("evidence_quality") or {}
    summary.cell(10, 1, "Verified observed inputs")
    summary.cell(10, 2, f"{eq.get('observed_pct', 0):.1f}%")
    summary.cell(11, 1, "Derived inputs")
    summary.cell(11, 2, f"{eq.get('derived_pct', 0):.1f}%")
    # Phase 6: reported as its OWN row -- never merged into "Verified observed"
    # (which would overstate this run's evidence quality) and never merged
    # into "Assumption" (which would discard the real, named-source signal).
    summary.cell(12, 1, "Source found, unverified inputs")
    summary.cell(12, 2, f"{eq.get('source_found_unverified_pct', 0):.1f}%")
    summary.cell(13, 1, "Assumption inputs")
    summary.cell(13, 2, f"{eq.get('assumption_pct', 0):.1f}%")

    summary.cell(15, 1, "Country").font = bold
    summary.cell(15, 2, "Included Demand (US$ Mn)").font = bold
    summary.cell(15, 3, "Related Demand (US$ Mn)").font = bold
    sr = 16
    related_by_country = report.get("related_demand_country_totals_usd_mn") or {}
    for country, total in (report["country_totals_usd_mn"] or {}).items():
        summary.cell(sr, 1, country)
        summary.cell(sr, 2, total)
        summary.cell(sr, 3, related_by_country.get(country, 0.0))
        sr += 1
    summary.cell(sr + 1, 1, "Global Total").font = bold
    summary.cell(sr + 1, 2, report["total_market_value_usd_mn"])
    summary.cell(sr + 1, 3, report.get("related_demand_value_usd_mn"))

    val = wb.create_sheet("Validation")
    validation = report.get("validation") or {}
    val.cell(1, 1, "Metric").font = bold
    val.cell(1, 2, "Value").font = bold
    vr = 2
    for key in (
        "deterministic_score", "plausibility_score", "arithmetic_ok",
        "coverage_pct", "coverage_threshold_pct", "included_demand_usd_mn",
        "related_demand_value_usd_mn", "residual_implied_usd_mn", "reported_market_view_usd_mn",
        "desk_research_reference_value", "reference_delta_pct",
    ):
        val.cell(vr, 1, key)
        val.cell(vr, 2, str(validation.get(key)))
        vr += 1
    for label, key in (
        ("Overlap flags", "overlap_flags"),
        ("Missing category flags", "missing_category_flags"),
        ("Dominant-bucket (unit mismatch risk) flags", "dominant_bucket_flags"),
        ("Country-cell outlier flags", "outlier_flags"),
        ("Intensity-fraction-clamped buckets", "intensity_clamped_buckets"),
        ("Unit contract violations (excluded from calculation)", "unit_contract_violations"),
        ("LLM CALL FAILURES (infrastructure, not genuine estimates)", "llm_call_failures"),
        ("Price plausibility flags", "price_plausibility_flags"),
        ("Unit mismatch flags", "unit_mismatch_flags"),
        ("Stale evidence source flags", "stale_source_flags"),
        ("Driver magnitude implausible flags", "driver_magnitude_flags"),
        ("Triangulation large-gap flags (informational)", "triangulation_large_gap_flags"),
        ("Magnitude cross-validation discrepancy flags", "magnitude_validation_flags"),
        ("Other problems", "problems"),
    ):
        vr += 1
        val.cell(vr, 1, label).font = bold
        for item in validation.get(key, []) or []:
            vr += 1
            val.cell(vr, 2, str(item))

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        wb.save(out_path)
        return out_path
    except PermissionError:
        # Most common cause: the file is currently open in Excel (a live
        # file-lock, not a real disk/permission problem) -- never let that
        # crash a run whose JSON output already saved successfully. Fall
        # back to a sibling filename instead of losing the run's output.
        alt_path = out_path.with_stem(out_path.stem + "_new")
        i = 2
        while alt_path.exists():
            alt_path = out_path.with_stem(f"{out_path.stem}_new{i}")
            i += 1
        wb.save(alt_path)
        return alt_path
